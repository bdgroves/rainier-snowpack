"""
fetch_modis.py
Downloads the latest MODIS MOD10A1 V061 daily snow cover tile (h09v04)
for the Mt. Rainier area, reprojects to WGS84, clips to Rainier bbox,
generates a snow cover map PNG, and saves summary stats to JSON.

Auth: EARTHDATA_TOKEN (repo secret) when it is still valid. Earthdata tokens
      last 60 days; when it has expired (it had, from May to Oct 2026, and
      the panel quietly showed an April image all summer), a fresh one is
      requested with EARTHDATA_USERNAME / EARTHDATA_PASSWORD or ~/.netrc.

Products: Terra MOD10A1 first, Aqua MYD10A1 as fallback (same format). Terra
          is near the end of its life; when it stops, Aqua carries on.

Snow: NDSI_Snow_Cover holds NDSI x 100 for clear land (0-100) and codes above
      200 for cloud, night, water and fill. A pixel counts as snow at NDSI >= 10
      (NSIDC's suggested threshold); snow % is of the clear land seen that day.
      (Until Oct 2026 every clear pixel counted as snow, so the % was really
      "% not cloudy".)

Cloud logic: skips granules with >80% cloud cover and falls back to
             the most recent clean pass within the last 14 days.
"""

import base64
import json
import logging
import netrc
import os
import sys
import time
import shutil
import requests
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.windows import from_bounds
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
LOG = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
PRODUCTS      = [("MOD10A1", "61", "Terra"), ("MYD10A1", "61", "Aqua")]
TILE          = "h09v04"
RAINIER_BBOX  = (-122.5, 46.0, -121.0, 47.5)
PARK_BBOX     = (-121.92, 46.73, -121.45, 47.01)   # Mt. Rainier National Park, roughly
SNOW_NDSI_MIN = 10
CLOUD_THRESH  = 80.0   # % cloud over the wider box above which a granule is unusable
PARK_CLOUD_MAX = 50.0  # ...and the park itself must be at least half visible
MAX_DAYS_BACK = 14     # how far back to search for a clean pass

RAW_DIR     = Path("data/raw/modis")
PROC_DIR    = Path("data/processed/modis")
OUT_DIR     = Path("outputs")
ARCHIVE_DIR = Path("data/modis_archive")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stations import STATIONS as _ST  # noqa: E402

STATIONS = [(s["name"], s["lon"], s["lat"]) for s in _ST]


def _jwt_exp(token):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp")
    except Exception:
        return None


def _credentials():
    user, pw = os.environ.get("EARTHDATA_USERNAME"), os.environ.get("EARTHDATA_PASSWORD")
    if user and pw:
        return user, pw
    for netrc_path in [Path.home() / "_netrc", Path.home() / ".netrc"]:
        if netrc_path.exists():
            try:
                auth = netrc.netrc(str(netrc_path)).authenticators("urs.earthdata.nasa.gov")
                if auth and auth[0] and auth[2]:
                    return auth[0], auth[2]
            except Exception as e:
                LOG.warning("netrc error: %s", e)
    return None


TOKEN_INFO = {}


def get_token():
    """A valid Earthdata bearer token: the secret if unexpired, else a fresh one."""
    token = os.environ.get("EARTHDATA_TOKEN", "").strip()
    exp = _jwt_exp(token) if token else None
    if token and exp and exp > time.time() + 3600:
        TOKEN_INFO.update(source="secret", expires=time.strftime("%Y-%m-%d", time.gmtime(exp)))
        LOG.info("Using EARTHDATA_TOKEN (expires %s)", TOKEN_INFO["expires"])
        return token
    if token:
        LOG.warning("EARTHDATA_TOKEN expired %s — requesting a new one",
                    time.strftime("%Y-%m-%d", time.gmtime(exp)) if exp else "(unreadable)")
    creds = _credentials()
    if creds:
        r = requests.post("https://urs.earthdata.nasa.gov/api/users/find_or_create_token",
                          auth=creds, timeout=60)
        if r.status_code == 200 and r.json().get("access_token"):
            new = r.json()["access_token"]
            exp = _jwt_exp(new)
            TOKEN_INFO.update(source="login", expires=time.strftime("%Y-%m-%d", time.gmtime(exp)) if exp else None)
            LOG.info("Got a fresh token from Earthdata login")
            return new
        LOG.error("Token request failed: HTTP %s %s", r.status_code, r.text[:200])
    if token:
        TOKEN_INFO.update(source="secret-expired")
        return token
    raise RuntimeError("No Earthdata token and no credentials to get one")


def search_granules(days_back=14):
    """CMR granules for our tile from Terra and Aqua, newest day first (Terra before Aqua on a day)."""
    end   = date.today()
    start = end - timedelta(days=days_back)
    LOG.info("Searching CMR: %s → %s ...", start, end)
    found = []
    for rank, (short, ver, sat) in enumerate(PRODUCTS):
        try:
            r = requests.get(
                "https://cmr.earthdata.nasa.gov/search/granules.json",
                params={"short_name": short, "version": ver, "temporal": f"{start},{end}",
                        "bounding_box": ",".join(map(str, RAINIER_BBOX)), "page_size": 40},
                timeout=60)
            r.raise_for_status()
            for e in r.json().get("feed", {}).get("entry", []):
                if TILE in e.get("title", ""):
                    e["_sat"], e["_product"], e["_rank"] = sat, short, rank
                    found.append(e)
        except Exception as ex:
            LOG.warning("CMR search %s failed: %s", short, ex)
    found.sort(key=lambda e: (-int(e["title"].split(".")[1][1:]), e["_rank"]))
    LOG.info("Found %d granules for tile %s", len(found), TILE)
    return found


def get_download_url(entry):
    """Extract protected download URL from a CMR entry."""
    for link in entry.get("links", []):
        href = link.get("href", "")
        if href.startswith("https") and href.endswith(".hdf"):
            return href
    return None


def parse_obs_date(title):
    """Parse observation date from granule title e.g. MOD10A1.A2026060..."""
    stamp = title.split(".")[1]           # A2026060
    year = int(stamp[1:5])
    doy  = int(stamp[5:8])
    return date(year, 1, 1) + timedelta(days=doy - 1)


def download_granule(token, url, out_path):
    """Download HDF file using NASA Earthdata bearer token."""
    if out_path.exists():
        LOG.info("Already downloaded: %s", out_path.name)
        return True

    LOG.info("Downloading %s ...", url.split("/")[-1])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, allow_redirects=True,
                     timeout=120, stream=True)
    LOG.info("Download status: %s", r.status_code)

    if r.status_code != 200:
        LOG.error("Download failed: HTTP %s", r.status_code)
        return False

    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)

    LOG.info("Saved: %s (%.1f MB)", out_path.name, out_path.stat().st_size / 1e6)
    return True


def reproject_to_wgs84(hdf_path, tif_path):
    """Reproject MODIS sinusoidal to WGS84 GeoTIFF."""
    if tif_path.exists():
        LOG.info("Already reprojected: %s", tif_path.name)
        return

    # band 1 = NDSI_Snow_Cover, band 2 = NDSI_Snow_Cover_Basic_QA (0 best … 3 poor)
    with rasterio.open(str(hdf_path)) as hdf:
        subs = hdf.subdatasets
    layer = next((sd for sd in subs if sd.endswith(":NDSI_Snow_Cover")),
                 f"HDF4_EOS:EOS_GRID:{hdf_path}:MOD_Grid_Snow_500m:NDSI_Snow_Cover")
    qa_layer = next((sd for sd in subs if sd.endswith(":NDSI_Snow_Cover_Basic_QA")), None)
    LOG.info("Reprojecting to WGS84 ...")
    tif_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(layer) as src:
        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:4326", src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update({"crs": "EPSG:4326", "transform": transform, "count": 2,
                       "width": width, "height": height, "driver": "GTiff"})
        with rasterio.open(tif_path, "w", **kwargs) as dst:
            for band, path in ((1, layer), (2, qa_layer)):
                if path is None:
                    dst.write(np.zeros((height, width), dtype=kwargs["dtype"]), 2)
                    continue
                with rasterio.open(path) as s2:
                    reproject(
                        source=rasterio.band(s2, 1),
                        destination=rasterio.band(dst, band),
                        src_transform=s2.transform,
                        src_crs=s2.crs,
                        dst_transform=transform,
                        dst_crs="EPSG:4326",
                        resampling=Resampling.nearest,
                    )
    LOG.info("Reprojected: %s", tif_path.name)


CLOUD_EDGE_PX = 1   # snow pixels touching cloud are set aside as doubtful
DOUBTFUL      = 248  # our own code (unused by NSIDC) for snow we don't trust


def read_clean(ds, bbox):
    """NDSI for a box with doubtful snow pixels recoded DOUBTFUL.

    Doubtful = NSIDC basic QA 'poor' (3), or touching a cloud pixel. Cloud edges
    are the classic MODIS false snow: the first run with a working token (28 Sep
    2026) showed "snow" over the lowlands west of the mountain, all at cloud
    edges. A stricter rule (QA ≥ 2, 2-px buffer) also threw out Rainier's and
    Adams's glaciers, so it was relaxed. Doubtful pixels count as neither snow
    nor clear land."""
    win = from_bounds(*bbox, ds.transform)
    data = ds.read(1, window=win).astype(np.int16)
    qa = ds.read(2, window=win) if ds.count >= 2 else np.zeros_like(data)
    cloud = data == 250
    near = cloud.copy()
    for _ in range(CLOUD_EDGE_PX):
        grown = near.copy()
        grown[1:, :] |= near[:-1, :]; grown[:-1, :] |= near[1:, :]
        grown[:, 1:] |= near[:, :-1]; grown[:, :-1] |= near[:, 1:]
        near = grown
    snow = (data >= SNOW_NDSI_MIN) & (data <= 100)
    doubtful = snow & ((qa == 3) | near)
    out = data.copy()
    out[doubtful] = DOUBTFUL
    return out, int(doubtful.sum())


def _area_stats(data):
    land  = (data >= 0) & (data <= 100)           # clear land, NDSI 0-100
    snow  = land & (data >= SNOW_NDSI_MIN)
    cloud = data == 250
    seen  = int(land.sum()) + int(cloud.sum())     # land pixels, clear or cloudy
    return {
        "pct_snow":  round(100 * snow.sum() / land.sum(), 1) if land.sum() else None,
        "pct_cloud": round(100 * cloud.sum() / seen, 1) if seen else None,
        "avg_ndsi":  round(float(data[snow].mean()), 1) if snow.any() else None,
        "snow_pixels": int(snow.sum()), "clear_pixels": int(land.sum()), "cloud_pixels": int(cloud.sum()),
        "doubtful_pixels": int((data == DOUBTFUL).sum()),
        "snow_km2": round(float(snow.sum()) * 0.2146, 0),   # 463 m MODIS pixel ≈ 0.2146 km²
    }


def compute_stats(tif_path, obs_date, days_ago=0, sat="Terra", product="MOD10A1"):
    """Snow cover for the wider Rainier box and for the park itself."""
    with rasterio.open(tif_path) as ds:
        region, masked = read_clean(ds, RAINIER_BBOX)
        park, _ = read_clean(ds, PARK_BBOX)
    reg, prk = _area_stats(region), _area_stats(park)
    reg["masked_snow_pixels"] = masked
    stats = dict(reg,
                 date=str(obs_date), tile=TILE, satellite=sat, product=product,
                 days_ago=days_ago, is_latest=days_ago <= 1,
                 snow_ndsi_min=SNOW_NDSI_MIN,
                 bbox=list(RAINIER_BBOX), park=dict(prk, bbox=list(PARK_BBOX)),
                 checked=date.today().isoformat(), token=TOKEN_INFO or None)
    LOG.info("%s %s: snow %s%% of clear land (park %s%%) | cloud %s%% | %d days ago",
             sat, obs_date, reg["pct_snow"], prk["pct_snow"], reg["pct_cloud"], days_ago)
    return stats


def make_map(tif_path, stats, obs_date):
    """Generate dark-themed snow cover map PNG."""
    with rasterio.open(tif_path) as ds:
        data, _ = read_clean(ds, RAINIER_BBOX)

    snow  = np.where((data >= SNOW_NDSI_MIN) & (data <= 100), data.astype(float), np.nan)
    # snow-free clear land is drawn dark green, cloud grey, everything else (water, night, fill) background
    cloud = np.where(data == 250, 1.0, np.nan)
    bare  = np.where((data >= 0) & (data < SNOW_NDSI_MIN), 1.0, np.nan)
    doubt = np.where(data == DOUBTFUL, 1.0, np.nan)

    fig, ax = plt.subplots(figsize=(10, 9))
    fig.patch.set_facecolor("#060f1e")
    ax.set_facecolor("#060f1e")

    extent = [RAINIER_BBOX[0], RAINIER_BBOX[2], RAINIER_BBOX[1], RAINIER_BBOX[3]]

    cmap_snow = plt.cm.Blues_r.copy()
    cmap_snow.set_bad(color="#0d1f3c")
    im = ax.imshow(snow, extent=extent, origin="upper", cmap=cmap_snow,
                   vmin=0, vmax=100, interpolation="nearest")

    ax.imshow(bare, extent=extent, origin="upper", cmap=mcolors.ListedColormap(["#2b3a2e"]),
              interpolation="nearest")
    ax.imshow(doubt, extent=extent, origin="upper", cmap=mcolors.ListedColormap(["#6b5a7a"]),
              interpolation="nearest")
    cmap_cloud = mcolors.ListedColormap(["#3a3a4a"])
    ax.imshow(cloud, extent=extent, origin="upper", cmap=cmap_cloud,
              alpha=0.6, interpolation="nearest")

    cbar = plt.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("NDSI Snow Cover %", color="#5a6a8a", fontsize=9, fontfamily="monospace")
    cbar.ax.yaxis.set_tick_params(color="#5a6a8a")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="#5a6a8a", fontsize=8)

    for name, lon, lat in STATIONS:
        ax.plot(lon, lat, "o", color="#ff8a65", markersize=7, zorder=5)
        ax.annotate(name, (lon, lat), textcoords="offset points", xytext=(6, 3),
                    color="#cdd6f4", fontsize=7, fontfamily="monospace")

    ax.plot(-121.7603, 46.8523, "*", color="#69f0ae", markersize=14, zorder=6)
    ax.annotate("Mt. Rainier\n14,410 ft", (-121.7603, 46.8523),
                textcoords="offset points", xytext=(8, -14),
                color="#69f0ae", fontsize=8, fontfamily="monospace", fontweight="bold")

    # Staleness note if not today's pass
    if stats.get("days_ago", 0) > 0:
        ax.text(0.98, 0.98,
                f"Last clean pass: {obs_date} ({stats['days_ago']}d ago)",
                transform=ax.transAxes, color="#ff8a65", fontsize=7,
                fontfamily="monospace", ha="right", va="top")

    ax.text(0.02, 0.02,
            f"Snow: {stats['pct_snow']}% of clear land (NDSI ≥ {SNOW_NDSI_MIN})  |  Cloud: {stats['pct_cloud']}%  |  "
            f"green = bare · grey = cloud · mauve = doubtful snow (cloud edge / poor QA)",
            transform=ax.transAxes, color="#5a6a8a", fontsize=7,
            fontfamily="monospace", va="bottom")

    ax.set_title(f"MODIS Snow Cover — {obs_date}\n{stats.get('product', 'MOD10A1')} V061 ({stats.get('satellite', 'Terra')}) · 500m · {TILE}",
                 color="#cdd6f4", fontsize=11, fontfamily="monospace", pad=12)
    ax.set_xlabel("Longitude", color="#5a6a8a", fontsize=8, fontfamily="monospace")
    ax.set_ylabel("Latitude",  color="#5a6a8a", fontsize=8, fontfamily="monospace")
    ax.tick_params(colors="#5a6a8a", labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor("#1e3a5f")

    plt.tight_layout()
    OUT_DIR.mkdir(exist_ok=True)
    out_png = OUT_DIR / "modis_snow_cover.png"
    plt.savefig(out_png, dpi=150, bbox_inches="tight",
                facecolor="#060f1e", edgecolor="none")
    plt.close()
    LOG.info("Map saved: %s", out_png)
    return out_png


def main():
    LOG.info("=== MODIS Snow Cover Fetch ===")
    token = get_token()
    granules = search_granules(days_back=MAX_DAYS_BACK)
    if not granules:
        LOG.error("No granules found in last %d days!", MAX_DAYS_BACK)
        raise SystemExit(1)

    today = date.today()
    selected, fallback, failures = None, None, 0
    for entry in granules:
        title = entry["title"]
        url = get_download_url(entry)
        if not url:
            LOG.warning("No download URL for %s, skipping", title)
            continue
        obs_date = parse_obs_date(title)
        days_ago = (today - obs_date).days
        LOG.info("--- %s %s (%d days ago) ---", entry["_sat"], obs_date, days_ago)
        hdf_path = RAW_DIR / title.split(".hdf")[0].replace(".", "_") / "granule.hdf"
        tif_path = PROC_DIR / f"snow_cover_{entry['_product']}_{obs_date}.tif"
        if not download_granule(token, url, hdf_path):
            failures += 1
            if failures >= 3 and not fallback:
                LOG.error("Three downloads in a row failed — check the Earthdata token/credentials")
                raise SystemExit(1)
            continue
        reproject_to_wgs84(hdf_path, tif_path)
        stats = compute_stats(tif_path, obs_date, days_ago, entry["_sat"], entry["_product"])
        pc = stats["park"]["pct_cloud"]
        if fallback is None or (pc is not None and (fallback[0]["park"]["pct_cloud"] or 100) > pc):
            fallback = (stats, tif_path, obs_date)
        if stats["pct_cloud"] is not None and stats["pct_cloud"] <= CLOUD_THRESH and pc is not None and pc <= PARK_CLOUD_MAX:
            LOG.info("✓ Clear enough: %s %s (%.1f%% cloud)", entry["_sat"], obs_date, stats["pct_cloud"])
            selected = (stats, tif_path, obs_date)
            break
        LOG.warning("%s%% cloud on %s — trying an earlier pass", stats["pct_cloud"], obs_date)

    if not selected:
        if not fallback:
            LOG.error("Nothing could be downloaded")
            raise SystemExit(1)
        LOG.warning("No clear-enough pass in %d days — using the one that saw the most of the park", MAX_DAYS_BACK)
        selected = fallback
        selected[0]["all_cloudy"] = True
    stats, tif, obs_date = selected

    PROC_DIR.mkdir(parents=True, exist_ok=True)
    (PROC_DIR / "modis_latest.json").write_text(json.dumps(stats, indent=2))
    out_png = make_map(tif, stats, obs_date)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_png = ARCHIVE_DIR / f"modis_{obs_date}.png"
    if not archive_png.exists():
        shutil.copy(out_png, archive_png)
        LOG.info("Archived: %s", archive_png.name)
    print(f"\n  {stats['satellite']} {obs_date} ({stats['days_ago']}d ago): snow {stats['pct_snow']}% of clear land, "
          f"park {stats['park']['pct_snow']}%, cloud {stats['pct_cloud']}%\n")


if __name__ == "__main__":
    main()

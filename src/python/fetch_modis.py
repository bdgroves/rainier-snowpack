"""
fetch_modis.py
Downloads the latest MODIS MOD10A1 V061 daily snow cover tile (h09v04)
for the Mt. Rainier area, reprojects to WGS84, clips to Rainier bbox,
generates a snow cover map PNG, and saves summary stats to JSON.

Auth: Reads EARTHDATA_TOKEN env var (set by GitHub Actions).
      Falls back to netrc credentials + token API for local runs.

Cloud logic: skips granules with >80% cloud cover and falls back to
             the most recent clean pass within the last 14 days.
"""

import json
import logging
import netrc
import os
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
CONCEPT_ID    = "C2565093311-NSIDC_CPRD"
TILE          = "h09v04"
RAINIER_BBOX  = (-122.5, 46.0, -121.0, 47.5)
CLOUD_THRESH  = 80.0   # % cloud cover above which a granule is considered unusable
MAX_DAYS_BACK = 14     # how far back to search for a clean pass

RAW_DIR  = Path("data/raw/modis")
PROC_DIR = Path("data/processed/modis")
OUT_DIR  = Path("outputs")

STATIONS = [
    ("Paradise",        -121.735, 46.786),
    ("Morse Lake",      -121.449, 46.952),
    ("Cayuse Pass",     -121.527, 46.870),
    ("Corral Pass",     -121.434, 47.014),
    ("Bumping Ridge",   -121.282, 46.836),
    ("Olallie Meadows", -121.543, 46.770),
    ("Cougar Mountain", -121.191, 46.900),
]


def get_token():
    """Get NASA Earthdata bearer token from env var or netrc."""
    token = os.environ.get("EARTHDATA_TOKEN")
    if token:
        LOG.info("Using EARTHDATA_TOKEN from environment")
        return token

    LOG.info("No env token, trying netrc...")
    for netrc_path in [Path.home() / "_netrc", Path.home() / ".netrc"]:
        if netrc_path.exists():
            try:
                creds = netrc.netrc(str(netrc_path))
                auth  = creds.authenticators("urs.earthdata.nasa.gov")
                if auth:
                    r = requests.post(
                        "https://urs.earthdata.nasa.gov/api/users/find_or_create_token",
                        auth=(auth[0], auth[2]), timeout=30
                    )
                    if r.status_code == 200:
                        token = r.json().get("access_token")
                        LOG.info("Got token from API")
                        return token
                    LOG.error("Token API failed: %s", r.text[:200])
            except Exception as e:
                LOG.warning("netrc error: %s", e)

    raise RuntimeError("Could not obtain Earthdata token")


def search_granules(days_back=14):
    """Search CMR for MOD10A1 granules over Rainier, newest first."""
    end   = date.today()
    start = end - timedelta(days=days_back)

    LOG.info("Searching CMR: %s → %s ...", start, end)
    r = requests.get(
        "https://cmr.earthdata.nasa.gov/search/granules",
        params={
            "concept_id":   CONCEPT_ID,
            "temporal":     f"{start},{end}",
            "bounding_box": f"{RAINIER_BBOX[0]},{RAINIER_BBOX[1]},{RAINIER_BBOX[2]},{RAINIER_BBOX[3]}",
            "page_size":    20,
        },
        headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    entries = r.json().get("feed", {}).get("entry", [])

    tile_entries = [e for e in entries if TILE in e.get("title", "")]
    tile_entries.sort(key=lambda e: e["title"], reverse=True)
    LOG.info("Found %d granules for tile %s", len(tile_entries), TILE)
    return tile_entries


def get_download_url(entry):
    """Extract protected download URL from a CMR entry."""
    for link in entry.get("links", []):
        href = link.get("href", "")
        if "prod-protected" in href and href.endswith(".hdf"):
            return href
    return None


def parse_obs_date(title):
    """Parse observation date from granule title e.g. MOD10A1.A2026060..."""
    year = int(title[9:13])
    doy  = int(title[13:16])
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

    layer = f"HDF4_EOS:EOS_GRID:{hdf_path}:MOD_Grid_Snow_500m:NDSI_Snow_Cover"
    LOG.info("Reprojecting to WGS84 ...")
    tif_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(layer) as src:
        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:4326", src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update({"crs": "EPSG:4326", "transform": transform,
                       "width": width, "height": height, "driver": "GTiff"})

        with rasterio.open(tif_path, "w", **kwargs) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.nearest,
            )
    LOG.info("Reprojected: %s", tif_path.name)


def compute_stats(tif_path, obs_date, days_ago=0):
    """Compute snow cover stats clipped to Rainier bbox."""
    with rasterio.open(tif_path) as ds:
        window = from_bounds(*RAINIER_BBOX, ds.transform)
        data   = ds.read(1, window=window)

    snow_pixels  = data[(data >= 0) & (data <= 100)]
    cloud_pixels = data[data == 250]
    total_valid  = len(data[(data != 255) & (data != 239)])

    pct_snow  = round(len(snow_pixels)  / total_valid * 100, 1) if total_valid > 0 else 0
    pct_cloud = round(len(cloud_pixels) / total_valid * 100, 1) if total_valid > 0 else 0
    avg_ndsi  = round(float(snow_pixels.mean()), 1) if len(snow_pixels) > 0 else 0

    stats = {
        "date":         str(obs_date),
        "tile":         TILE,
        "pct_snow":     pct_snow,
        "pct_cloud":    pct_cloud,
        "avg_ndsi":     avg_ndsi,
        "snow_pixels":  int(len(snow_pixels)),
        "cloud_pixels": int(len(cloud_pixels)),
        "total_pixels": int(total_valid),
        "days_ago":     days_ago,
        "is_latest":    days_ago == 0,
    }

    LOG.info("Snow: %.1f%% | Cloud: %.1f%% | Avg NDSI: %.1f | %d days ago",
             pct_snow, pct_cloud, avg_ndsi, days_ago)
    return stats


def make_map(tif_path, stats, obs_date):
    """Generate dark-themed snow cover map PNG."""
    with rasterio.open(tif_path) as ds:
        window = from_bounds(*RAINIER_BBOX, ds.transform)
        data   = ds.read(1, window=window)

    snow  = np.where((data >= 0) & (data <= 100), data.astype(float), np.nan)
    cloud = np.where(data == 250, 1.0, np.nan)

    fig, ax = plt.subplots(figsize=(10, 9))
    fig.patch.set_facecolor("#060f1e")
    ax.set_facecolor("#060f1e")

    extent = [RAINIER_BBOX[0], RAINIER_BBOX[2], RAINIER_BBOX[1], RAINIER_BBOX[3]]

    cmap_snow = plt.cm.Blues_r.copy()
    cmap_snow.set_bad(color="#0d1f3c")
    im = ax.imshow(snow, extent=extent, origin="upper", cmap=cmap_snow,
                   vmin=0, vmax=100, interpolation="nearest")

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

    ax.plot(-121.7269, 46.8523, "*", color="#69f0ae", markersize=14, zorder=6)
    ax.annotate("Mt. Rainier\n14,411 ft", (-121.7269, 46.8523),
                textcoords="offset points", xytext=(8, -14),
                color="#69f0ae", fontsize=8, fontfamily="monospace", fontweight="bold")

    # Staleness note if not today's pass
    if stats.get("days_ago", 0) > 0:
        ax.text(0.98, 0.98,
                f"Last clean pass: {obs_date} ({stats['days_ago']}d ago)",
                transform=ax.transAxes, color="#ff8a65", fontsize=7,
                fontfamily="monospace", ha="right", va="top")

    ax.text(0.02, 0.02,
            f"Snow: {stats['pct_snow']}%  |  Cloud: {stats['pct_cloud']}%  |  Avg NDSI: {stats['avg_ndsi']}",
            transform=ax.transAxes, color="#5a6a8a", fontsize=7,
            fontfamily="monospace", va="bottom")

    ax.set_title(f"MODIS Snow Cover — {obs_date}\nMOD10A1 V061 · 500m · {TILE}",
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

    # Get all recent granules newest first
    granules = search_granules(days_back=MAX_DAYS_BACK)
    if not granules:
        LOG.error("No granules found in last %d days!", MAX_DAYS_BACK)
        raise SystemExit(1)

    today = date.today()

    # Walk through granules newest → oldest, stop at first clean pass
    selected_stats    = None
    selected_tif      = None
    selected_obs_date = None

    for entry in granules:
        title = entry["title"]
        url   = get_download_url(entry)
        if not url:
            LOG.warning("No download URL for %s, skipping", title)
            continue

        obs_date = parse_obs_date(title)
        days_ago = (today - obs_date).days
        year     = obs_date.year
        doy      = obs_date.timetuple().tm_yday

        LOG.info("--- Trying %s (%d days ago) ---", obs_date, days_ago)

        hdf_path = RAW_DIR / f"MOD10A1.A{year}{doy:03d}.{TILE}.hdf"
        tif_path = PROC_DIR / f"snow_cover_{obs_date}.tif"

        if not download_granule(token, url, hdf_path):
            LOG.warning("Download failed for %s, trying next", obs_date)
            continue

        reproject_to_wgs84(hdf_path, tif_path)
        stats = compute_stats(tif_path, obs_date, days_ago=days_ago)

        if stats["pct_cloud"] > CLOUD_THRESH:
            LOG.warning("%.1f%% cloud cover on %s — too cloudy, trying earlier pass",
                        stats["pct_cloud"], obs_date)
            continue

        # Clean enough — use this one
        LOG.info("✓ Clean pass found: %s (%.1f%% cloud)", obs_date, stats["pct_cloud"])
        selected_stats    = stats
        selected_tif      = tif_path
        selected_obs_date = obs_date
        break

    if not selected_stats:
        LOG.error("No clean pass found in last %d days! All granules >%.0f%% cloud.",
                  MAX_DAYS_BACK, CLOUD_THRESH)
        # Fall back to most recent regardless
        LOG.info("Falling back to most recent granule despite cloud cover...")
        entry    = granules[0]
        title    = entry["title"]
        url      = get_download_url(entry)
        obs_date = parse_obs_date(title)
        days_ago = (today - obs_date).days
        year     = obs_date.year
        doy      = obs_date.timetuple().tm_yday
        hdf_path = RAW_DIR / f"MOD10A1.A{year}{doy:03d}.{TILE}.hdf"
        tif_path = PROC_DIR / f"snow_cover_{obs_date}.tif"
        download_granule(token, url, hdf_path)
        reproject_to_wgs84(hdf_path, tif_path)
        selected_stats    = compute_stats(tif_path, obs_date, days_ago=days_ago)
        selected_tif      = tif_path
        selected_obs_date = obs_date
        selected_stats["all_cloudy"] = True

    # Save JSON
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    (PROC_DIR / "modis_latest.json").write_text(json.dumps(selected_stats, indent=2))
    LOG.info("Stats saved to modis_latest.json")

    # Map
    make_map(selected_tif, selected_stats, selected_obs_date)

    # Summary
    days_ago = selected_stats["days_ago"]
    staleness = f"(most recent clean pass — {days_ago}d ago)" if days_ago > 0 else "(today's pass)"
    print("\n" + "="*55)
    print(f"  MODIS Snow Cover — {selected_obs_date} {staleness}")
    print("="*55)
    print(f"  Snow cover:   {selected_stats['pct_snow']}% of Rainier bbox")
    print(f"  Cloud cover:  {selected_stats['pct_cloud']}%")
    print(f"  Avg NDSI:     {selected_stats['avg_ndsi']}")
    print(f"  Snow pixels:  {selected_stats['snow_pixels']:,}")
    print("="*55)

    LOG.info("=== Done! ===")


if __name__ == "__main__":
    main()

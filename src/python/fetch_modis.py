"""
fetch_modis.py
Downloads the latest MODIS MOD10A1 V061 daily snow cover tile (h09v04)
for the Mt. Rainier area, reprojects to WGS84, clips to Rainier bbox,
generates a snow cover map PNG, and saves summary stats to JSON.

Auth: NASA Earthdata credentials via ~/.netrc or C:/Users/<user>/_netrc
CMR search is unauthenticated (public API).
Authenticated session used only for protected file downloads.
Download handles NASA OAuth redirect chain manually.
"""

import json
import logging
import netrc
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
CONCEPT_ID   = "C2565093311-NSIDC_CPRD"
TILE         = "h09v04"
RAINIER_BBOX = (-122.5, 46.0, -121.0, 47.5)

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


def get_credentials():
    """Read NASA Earthdata credentials from _netrc or .netrc."""
    for netrc_path in [Path.home() / "_netrc", Path.home() / ".netrc"]:
        if netrc_path.exists():
            try:
                creds = netrc.netrc(str(netrc_path))
                auth = creds.authenticators("urs.earthdata.nasa.gov")
                if auth:
                    LOG.info("Credentials loaded from %s", netrc_path)
                    return auth[0], auth[2]
            except Exception as e:
                LOG.warning("Could not read %s: %s", netrc_path, e)
    raise FileNotFoundError("No netrc file with urs.earthdata.nasa.gov credentials found")


def find_latest_granule(days_back=7):
    """Search CMR (public, no auth) for most recent MOD10A1 granule over Rainier."""
    end   = date.today()
    start = end - timedelta(days=days_back)

    LOG.info("Searching CMR: %s → %s ...", start, end)
    r = requests.get(
        "https://cmr.earthdata.nasa.gov/search/granules",
        params={
            "concept_id":   CONCEPT_ID,
            "temporal":     f"{start},{end}",
            "bounding_box": f"{RAINIER_BBOX[0]},{RAINIER_BBOX[1]},{RAINIER_BBOX[2]},{RAINIER_BBOX[3]}",
            "page_size":    10,
        },
        headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    entries = r.json().get("feed", {}).get("entry", [])

    tile_entries = [e for e in entries if TILE in e.get("title", "")]
    if not tile_entries:
        LOG.warning("No granules found for tile %s in last %d days", TILE, days_back)
        return None, None

    tile_entries.sort(key=lambda e: e["title"], reverse=True)
    latest = tile_entries[0]
    LOG.info("Latest granule: %s", latest["title"])

    download_url = None
    for link in latest.get("links", []):
        href = link.get("href", "")
        if "prod-protected" in href and href.endswith(".hdf"):
            download_url = href
            break

    return latest["title"], download_url


def download_granule(username, password, url, out_path):
    """Download HDF file handling NASA Earthdata OAuth redirect chain."""
    if out_path.exists():
        LOG.info("Already downloaded: %s", out_path.name)
        return True

    LOG.info("Downloading %s ...", url.split("/")[-1])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # NASA Earthdata Cloud uses OAuth redirects — follow chain manually with auth
    with requests.Session() as s:
        s.auth = (username, password)

        r = s.get(url, allow_redirects=False, timeout=30)
        hops = 0
        while r.status_code in (301, 302, 303, 307, 308) and hops < 10:
            redirect_url = r.headers.get("location", "")
            LOG.info("Redirect %d → %s", hops + 1, redirect_url[:80])
            r = s.get(redirect_url, allow_redirects=False, timeout=30)
            hops += 1

        if r.status_code != 200:
            LOG.error("Download failed: HTTP %s after %d redirects", r.status_code, hops)
            return False

        with open(out_path, "wb") as f:
            f.write(r.content)

    LOG.info("Saved: %s (%.1f MB)", out_path.name, out_path.stat().st_size / 1e6)
    return True


def reproject_to_wgs84(hdf_path, tif_path):
    """Reproject MODIS sinusoidal projection to WGS84 GeoTIFF."""
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


def compute_stats(tif_path, obs_date):
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
    }

    LOG.info("Snow: %.1f%% | Cloud: %.1f%% | Avg NDSI: %.1f", pct_snow, pct_cloud, avg_ndsi)
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

    username, password = get_credentials()
    LOG.info("Authenticated as: %s", username)

    # Search CMR — no auth needed
    title, download_url = find_latest_granule()
    if not download_url:
        LOG.error("No download URL found!")
        raise SystemExit(1)

    # Parse observation date from title e.g. MOD10A1.A2026060 = day 60 of 2026
    year     = int(title[9:13])
    doy      = int(title[13:16])
    obs_date = date(year, 1, 1) + timedelta(days=doy - 1)
    LOG.info("Observation date: %s", obs_date)

    # Download with OAuth redirect handling
    hdf_path = RAW_DIR / f"MOD10A1.A{year}{doy:03d}.{TILE}.hdf"
    if not download_granule(username, password, download_url, hdf_path):
        raise SystemExit(1)

    # Reproject
    tif_path = PROC_DIR / f"snow_cover_{obs_date}.tif"
    reproject_to_wgs84(hdf_path, tif_path)

    # Stats + JSON
    stats = compute_stats(tif_path, obs_date)
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    (PROC_DIR / "modis_latest.json").write_text(json.dumps(stats, indent=2))

    # Map
    make_map(tif_path, stats, obs_date)

    print("\n" + "="*55)
    print(f"  MODIS Snow Cover — {obs_date}")
    print("="*55)
    print(f"  Snow cover:   {stats['pct_snow']}% of Rainier bbox")
    print(f"  Cloud cover:  {stats['pct_cloud']}%")
    print(f"  Avg NDSI:     {stats['avg_ndsi']}")
    print(f"  Snow pixels:  {stats['snow_pixels']:,}")
    print("="*55)

    LOG.info("=== Done! ===")


if __name__ == "__main__":
    main()

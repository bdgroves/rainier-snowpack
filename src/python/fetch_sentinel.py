“””
fetch_sentinel.py
Downloads the most recent clear Sentinel-2 true-color image over Mt. Rainier
via the Microsoft Planetary Computer STAC API (no auth required for search;
uses token signing for asset download).

Archives dated PNGs to data/sentinel_archive/ and saves the latest to
outputs/sentinel_latest.png + dashboard/sentinel_latest.png.

Cloud threshold: skips scenes with >30% cloud cover (Sentinel is 10m —
we want clear passes only, stricter than MODIS 80%).
“””

import json
import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import matplotlib.pyplot as plt
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.windows import from_bounds
import io

logging.basicConfig(level=logging.INFO, format=”%(levelname)-8s %(message)s”)
LOG = logging.getLogger(**name**)

RAINIER_BBOX   = (-122.5, 46.0, -121.0, 47.5)   # lon_min, lat_min, lon_max, lat_max
CLOUD_THRESH   = 30.0
MAX_DAYS_BACK  = 30    # Sentinel has 5-day revisit so look back further

STAC_URL   = “https://planetarycomputer.microsoft.com/api/stac/v1”
TOKEN_URL  = “https://planetarycomputer.microsoft.com/api/sas/v1/token”
COLLECTION = “sentinel-2-l2a”

OUT_DIR      = Path(“outputs”)
DASH_DIR     = Path(“dashboard”)
ARCHIVE_DIR  = Path(“data/sentinel_archive”)
PROC_DIR     = Path(“data/processed”)

STATIONS = [
(“Paradise”,        -121.735, 46.786),
(“Morse Lake”,      -121.449, 46.952),
(“Cayuse Pass”,     -121.527, 46.870),
(“Corral Pass”,     -121.434, 47.014),
(“Bumping Ridge”,   -121.282, 46.836),
(“Olallie Meadows”, -121.543, 46.770),
(“Cougar Mountain”, -121.191, 46.900),
]

def search_scenes(client, days_back: int = MAX_DAYS_BACK) -> list:
“”“Search Planetary Computer STAC for recent Sentinel-2 scenes.”””
end   = date.today()
start = end - timedelta(days=days_back)

```
bbox = [RAINIER_BBOX[0], RAINIER_BBOX[1], RAINIER_BBOX[2], RAINIER_BBOX[3]]

r = client.post(
    f"{STAC_URL}/search",
    json={
        "collections": [COLLECTION],
        "bbox":        bbox,
        "datetime":    f"{start}T00:00:00Z/{end}T23:59:59Z",
        "query":       {"eo:cloud_cover": {"lt": CLOUD_THRESH}},
        "sortby":      [{"field": "datetime", "direction": "desc"}],
        "limit":       10,
    },
    timeout=30,
)
r.raise_for_status()
items = r.json().get("features", [])
LOG.info("Found %d scenes with <%.0f%% cloud in last %d days",
         len(items), CLOUD_THRESH, days_back)
return items
```

def get_signed_url(client, item: dict, asset: str) -> str:
“”“Sign a Planetary Computer asset URL for download.”””
href = item[“assets”][asset][“href”]
# Extract collection/item path for token
collection = item[“collection”]
r = client.get(f”{TOKEN_URL}/{collection}”, timeout=15)
if r.status_code == 200:
token = r.json().get(“token”, “”)
return f”{href}?{token}”
return href  # fall back to unsigned (may fail for some assets)

def download_band(client, url: str) -> bytes:
“”“Download a COG band as bytes.”””
r = client.get(url, timeout=120, follow_redirects=True)
r.raise_for_status()
return r.content

def make_true_color_png(item: dict, client: httpx.Client, out_path: Path) -> bool:
“””
Download B04 (red), B03 (green), B02 (blue) bands and produce a
true-color PNG clipped to the Rainier bbox.
“””
try:
bands = {}
for band, asset_key in [(“red”, “B04”), (“green”, “B03”), (“blue”, “B02”)]:
LOG.info(”  Downloading %s (%s)…”, band, asset_key)
if asset_key not in item[“assets”]:
LOG.warning(”  Asset %s not found in scene”, asset_key)
return False

```
        signed = get_signed_url(client, item, asset_key)
        data = download_band(client, signed)

        with rasterio.open(io.BytesIO(data)) as src:
            # Reproject to WGS84 and clip to bbox
            transform, width, height = calculate_default_transform(
                src.crs, "EPSG:4326", src.width, src.height, *src.bounds
            )
            arr = np.zeros((height, width), dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=arr,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.bilinear,
            )

            # Clip to Rainier bbox
            from rasterio.transform import rowcol
            import affine
            new_ds_meta = src.meta.copy()
            new_ds_meta.update({"crs": "EPSG:4326", "transform": transform,
                                "width": width, "height": height})

            # Simple bbox clip using pixel math
            lon_min, lat_min, lon_max, lat_max = RAINIER_BBOX
            row_start = max(0, int((lat_max - transform.f) / transform.e))
            row_end   = min(height, int((lat_min - transform.f) / transform.e))
            col_start = max(0, int((lon_min - transform.c) / transform.a))
            col_end   = min(width, int((lon_max - transform.c) / transform.a))

            bands[band] = arr[row_start:row_end, col_start:col_end]

    if not all(b in bands for b in ["red", "green", "blue"]):
        return False

    # Normalize to 0-1 (Sentinel-2 L2A reflectance is 0-10000)
    def norm(arr):
        p2, p98 = np.percentile(arr[arr > 0], [2, 98]) if arr[arr > 0].size > 0 else (0, 1)
        return np.clip((arr - p2) / (p98 - p2 + 1e-6), 0, 1)

    rgb = np.dstack([norm(bands["red"]), norm(bands["green"]), norm(bands["blue"])])

    # Plot with dark styling
    obs_date = item["properties"]["datetime"][:10]
    cloud_pct = item["properties"].get("eo:cloud_cover", 0)

    fig, ax = plt.subplots(figsize=(10, 9))
    fig.patch.set_facecolor("#060f1e")
    ax.set_facecolor("#060f1e")

    ax.imshow(rgb, extent=[RAINIER_BBOX[0], RAINIER_BBOX[2],
                           RAINIER_BBOX[1], RAINIER_BBOX[3]],
              origin="upper", interpolation="bilinear")

    # Station markers
    for name, lon, lat in STATIONS:
        ax.plot(lon, lat, "o", color="#ff8a65", markersize=7, zorder=5)
        ax.annotate(name, (lon, lat), textcoords="offset points", xytext=(6, 3),
                    color="#cdd6f4", fontsize=7, fontfamily="monospace")

    # Rainier summit
    ax.plot(-121.7269, 46.8523, "*", color="#69f0ae", markersize=14, zorder=6)
    ax.annotate("Mt. Rainier\n14,411 ft", (-121.7269, 46.8523),
                textcoords="offset points", xytext=(8, -14),
                color="#69f0ae", fontsize=8, fontfamily="monospace", fontweight="bold")

    ax.text(0.02, 0.02,
            f"Cloud: {cloud_pct:.1f}%  |  Sentinel-2 L2A · 10m · True Color",
            transform=ax.transAxes, color="#5a6a8a", fontsize=7,
            fontfamily="monospace", va="bottom")

    ax.set_title(f"Sentinel-2 True Color — {obs_date}\nSentinel-2 L2A · 10m · Copernicus",
                 color="#cdd6f4", fontsize=11, fontfamily="monospace", pad=12)
    ax.set_xlabel("Longitude", color="#5a6a8a", fontsize=8, fontfamily="monospace")
    ax.set_ylabel("Latitude",  color="#5a6a8a", fontsize=8, fontfamily="monospace")
    ax.tick_params(colors="#5a6a8a", labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor("#1e3a5f")

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor="#060f1e", edgecolor="none")
    plt.close()
    LOG.info("Saved: %s", out_path)
    return True

except Exception as e:
    LOG.warning("Failed to build PNG: %s", e)
    plt.close()
    return False
```

def main():
LOG.info(”=== Sentinel-2 Fetch ===”)

```
OUT_DIR.mkdir(exist_ok=True)
ARCH_DIR = ARCHIVE_DIR
ARCH_DIR.mkdir(parents=True, exist_ok=True)
PROC_DIR.mkdir(parents=True, exist_ok=True)

with httpx.Client(timeout=30) as client:
    scenes = search_scenes(client)

    if not scenes:
        LOG.warning("No clear Sentinel-2 scenes found in last %d days", MAX_DAYS_BACK)
        raise SystemExit(0)

    for item in scenes:
        obs_date  = item["properties"]["datetime"][:10]
        cloud_pct = item["properties"].get("eo:cloud_cover", 100)
        scene_id  = item["id"]

        LOG.info("Trying %s (%.1f%% cloud)...", obs_date, cloud_pct)

        out_png     = OUT_DIR / "sentinel_latest.png"
        archive_png = ARCH_DIR / f"sentinel_{obs_date}.png"

        # Skip if already archived
        if archive_png.exists():
            LOG.info("Already archived: %s — using it", archive_png.name)
            shutil.copy(archive_png, out_png)
            shutil.copy(out_png, DASH_DIR / "sentinel_latest.png")
            break

        success = make_true_color_png(item, client, out_png)

        if success:
            # Archive it
            shutil.copy(out_png, archive_png)
            LOG.info("Archived: %s", archive_png.name)

            # Copy to dashboard
            DASH_DIR.mkdir(exist_ok=True)
            shutil.copy(out_png, DASH_DIR / "sentinel_latest.png")

            # Save metadata JSON
            meta = {
                "date":       obs_date,
                "scene_id":   scene_id,
                "cloud_pct":  round(cloud_pct, 1),
                "resolution": "10m",
                "platform":   item["properties"].get("platform", "sentinel-2"),
            }
            (PROC_DIR / "sentinel_latest.json").write_text(json.dumps(meta, indent=2))
            (DASH_DIR / "sentinel_latest.json").write_text(json.dumps(meta, indent=2))

            print(f"\n{'='*55}")
            print(f"  Sentinel-2 — {obs_date}")
            print(f"{'='*55}")
            print(f"  Cloud cover:  {cloud_pct:.1f}%")
            print(f"  Scene:        {scene_id[:40]}...")
            print(f"  Saved:        outputs/sentinel_latest.png")
            print(f"{'='*55}")
            LOG.info("=== Done! ===")
            break
        else:
            LOG.warning("PNG generation failed for %s, trying next scene", obs_date)
    else:
        LOG.error("All scenes failed PNG generation")
        raise SystemExit(1)
```

if **name** == “**main**”:
main()

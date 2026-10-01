# fetch_sentinel.py
# Downloads the most recent clear Sentinel-2 true-color image over Mt. Rainier
# via the Microsoft Planetary Computer STAC API.
# Archives dated PNGs to data/sentinel_archive/
# Cloud threshold: skips scenes with >30% cloud cover

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
import io

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
LOG = logging.getLogger(__name__)

RAINIER_BBOX  = (-122.05, 46.66, -121.40, 47.04)   # the mountain and its ring of valleys
SUMMIT        = (-121.7603, 46.8523)
MAX_NODATA    = 5.0    # % of the tile outside the swath; above this the mountain may be cut off
CLOUD_THRESH  = 30.0
MAX_DAYS_BACK = 30

STAC_URL   = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN_URL  = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
COLLECTION = "sentinel-2-l2a"

OUT_DIR     = Path("outputs")
DASH_DIR    = Path("dashboard")
ARCHIVE_DIR = Path("data/sentinel_archive")
PROC_DIR    = Path("data/processed")

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stations import STATIONS as _ST  # noqa: E402

STATIONS = [(s["name"], s["lon"], s["lat"]) for s in _ST]


def search_scenes(client, days_back=MAX_DAYS_BACK):
    end   = date.today()
    start = end - timedelta(days=days_back)
    r = client.post(
        f"{STAC_URL}/search",
        json={
            "collections": [COLLECTION],
            # the tile must contain the summit and be (nearly) full — until Oct 2026 this
            # searched a 1.5° box, could pick a tile that only clipped its corner,
            # and published a mostly black image
            "intersects":  {"type": "Point", "coordinates": list(SUMMIT)},
            "datetime":    f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query":       {"eo:cloud_cover": {"lt": CLOUD_THRESH},
                            "s2:nodata_pixel_percentage": {"lt": MAX_NODATA}},
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


def get_signed_url(client, item, asset):
    href = item["assets"][asset]["href"]
    collection = item["collection"]
    r = client.get(f"{TOKEN_URL}/{collection}", timeout=15)
    if r.status_code == 200:
        token = r.json().get("token", "")
        return f"{href}?{token}"
    return href


def download_band(client, url):
    r = client.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r.content


def make_true_color_png(item, client, out_path):
    """Read just the Rainier window from the tile's 8-bit true-colour COG (the
    'visual' asset), warped to lon/lat at ~50 m. No full-tile downloads."""
    try:
        from rasterio.transform import from_bounds as _tfb
        if "visual" not in item["assets"]:
            LOG.warning("  no visual asset")
            return False
        href = get_signed_url(client, item, "visual")
        w = int((RAINIER_BBOX[2] - RAINIER_BBOX[0]) / 0.0005)
        h = int((RAINIER_BBOX[3] - RAINIER_BBOX[1]) / 0.00035)
        dst_t = _tfb(*RAINIER_BBOX, w, h)
        rgb = np.zeros((3, h, w), dtype=np.uint8)
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MULTIRANGE="YES"):
            with rasterio.open(href) as src:
                for b in range(3):
                    reproject(source=rasterio.band(src, b + 1), destination=rgb[b],
                              src_transform=src.transform, src_crs=src.crs,
                              dst_transform=dst_t, dst_crs="EPSG:4326",
                              resampling=Resampling.average, src_nodata=0, dst_nodata=0)
        valid = (rgb.sum(axis=0) > 0).mean()
        if valid < 0.9:
            LOG.warning("  only %.0f%% of the Rainier window has data — skipping", 100 * valid)
            return False
        img = np.clip(np.moveaxis(rgb, 0, -1).astype(np.float32) / 255.0 * 1.1, 0, 1)

        obs_date  = item["properties"]["datetime"][:10]
        cloud_pct = item["properties"].get("eo:cloud_cover", 0)

        fig, ax = plt.subplots(figsize=(11, 8.5))
        fig.patch.set_facecolor("#060f1e")
        ax.set_facecolor("#060f1e")
        ax.imshow(img,
                  extent=[RAINIER_BBOX[0], RAINIER_BBOX[2], RAINIER_BBOX[1], RAINIER_BBOX[3]],
                  origin="upper", interpolation="bilinear")

        for name, lon, lat in STATIONS:
            if not (RAINIER_BBOX[0] <= lon <= RAINIER_BBOX[2] and RAINIER_BBOX[1] <= lat <= RAINIER_BBOX[3]):
                continue
            ax.plot(lon, lat, "o", color="#ff8a65", markersize=7, zorder=5)
            ax.annotate(name, (lon, lat), textcoords="offset points", xytext=(6, 3),
                        color="#cdd6f4", fontsize=7, fontfamily="monospace")

        ax.plot(-121.7603, 46.8523, "*", color="#69f0ae", markersize=14, zorder=6)
        ax.annotate("Mt. Rainier\n14,410 ft", (-121.7603, 46.8523),
                    textcoords="offset points", xytext=(8, -14),
                    color="#69f0ae", fontsize=8, fontfamily="monospace", fontweight="bold")
        ax.text(0.02, 0.02,
                "Tile cloud: %.1f%%  |  Sentinel-2 L2A true colour, shown at ~50 m" % cloud_pct,
                transform=ax.transAxes, color="#5a6a8a", fontsize=7,
                fontfamily="monospace", va="bottom")
        ax.set_title("Sentinel-2 True Color -- %s\nSentinel-2 L2A 10m Copernicus" % obs_date,
                     color="#cdd6f4", fontsize=11, fontfamily="monospace", pad=12)
        ax.set_xlabel("Longitude", color="#5a6a8a", fontsize=8, fontfamily="monospace")
        ax.set_ylabel("Latitude",  color="#5a6a8a", fontsize=8, fontfamily="monospace")
        ax.tick_params(colors="#5a6a8a", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#1e3a5f")

        plt.tight_layout()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(out_path, dpi=130, bbox_inches="tight",
                    facecolor="#060f1e", edgecolor="none", pil_kwargs={"quality": 85})
        plt.close()
        LOG.info("Saved: %s", out_path)
        return True

    except Exception as e:
        LOG.warning("Failed to build PNG: %s", e)
        plt.close()
        return False


def main():
    LOG.info("=== Sentinel-2 Fetch ===")
    for d in [OUT_DIR, ARCHIVE_DIR, PROC_DIR, DASH_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=30) as client:
        scenes = search_scenes(client)
        if not scenes:
            LOG.warning("No clear scenes found -- exiting")
            raise SystemExit(0)

        for item in scenes:
            obs_date  = item["properties"]["datetime"][:10]
            cloud_pct = item["properties"].get("eo:cloud_cover", 100)
            scene_id  = item["id"]
            LOG.info("Trying %s (%.1f%% cloud)...", obs_date, cloud_pct)

            # JPEG: a true-colour photo is ~4x smaller than PNG, and the archive is committed to git
            out_png     = OUT_DIR / "sentinel_latest.jpg"
            archive_png = ARCHIVE_DIR / ("sentinel_" + obs_date + ".jpg")

            if archive_png.exists():
                LOG.info("Already archived: %s", archive_png.name)
                shutil.copy(archive_png, out_png)
                shutil.copy(out_png, DASH_DIR / out_png.name)
                break

            if make_true_color_png(item, client, out_png):
                shutil.copy(out_png, archive_png)
                shutil.copy(out_png, DASH_DIR / out_png.name)
                meta = {
                    "image":      out_png.name,
                    "date":       obs_date,
                    "scene_id":   scene_id,
                    "cloud_pct":  round(cloud_pct, 1),
                    "resolution": "10m",
                    "platform":   item["properties"].get("platform", "sentinel-2"),
                }
                (PROC_DIR / "sentinel_latest.json").write_text(json.dumps(meta, indent=2))
                LOG.info("Archived: %s", archive_png.name)
                print("\n" + "="*55)
                print("  Sentinel-2 -- %s  cloud: %.1f%%" % (obs_date, cloud_pct))
                print("="*55)
                LOG.info("=== Done! ===")
                break
            else:
                LOG.warning("Failed for %s, trying next", obs_date)
        else:
            LOG.error("All scenes failed")
            raise SystemExit(1)


if __name__ == "__main__":
    main()

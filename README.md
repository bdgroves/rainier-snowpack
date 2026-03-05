# 🏔️ Mt. Rainier Snowpack Monitor

> Real-time snowpack intelligence for the Mt. Rainier watershed — ground sensors + satellite imagery, fully automated.

**[🌐 Live Dashboard →](https://bdgroves.github.io/rainier-snowpack/)**

![MODIS Snow Cover](dashboard/modis_snow_cover.png)

---

## What This Is

A fully automated snowpack monitoring system that fuses two data sources every hour:

- **7 SNOTEL ground stations** — SWE, snow depth, and temperature from the NRCS AWDB network surrounding Mt. Rainier
- **NASA MODIS Terra satellite** — MOD10A1 V061 daily snow cover at 500m resolution over the Pacific Northwest (tile h09v04)

The pipeline fetches, processes, analyzes, and deploys a live dashboard to GitHub Pages without any human intervention.

---

## Dashboard Features

| Panel | Description |
|---|---|
| **KPI Strip** | Basin avg SWE · avg depth · peak SWE · avg temperature · stations freezing |
| **SWE Time Series** | Basin average snow water equivalent across Water Year 2026 |
| **Station Cards** | Per-station SWE, depth, and temperature with relative bar charts |
| **Elevation Ladder** | SWE ranked by station elevation |
| **Temperature Panel** | Current temps sorted coldest → warmest with freeze/thaw indicator |
| **48-Hour Diurnal Chart** | Hourly temperature traces for all 7 stations over the past 48 hours |
| **MODIS Satellite Map** | NASA snow cover map with SNOTEL station overlays and summit marker |

---

## Data Sources

### NRCS SNOTEL — AWDB REST API
```
https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data
```

| Station | Triplet | Elevation |
|---|---|---|
| Paradise | 679:WA:SNTL | 5,150 ft |
| Morse Lake | 642:WA:SNTL | 5,400 ft |
| Cayuse Pass | 1085:WA:SNTL | 5,260 ft |
| Corral Pass | 418:WA:SNTL | 5,810 ft |
| Bumping Ridge | 375:WA:SNTL | 4,600 ft |
| Olallie Meadows | 672:WA:SNTL | 4,010 ft |
| Cougar Mountain | 420:WA:SNTL | 3,210 ft |

Variables fetched: `WTEQ` (SWE) · `SNWD` (snow depth) · `TOBS` (temperature) · `PRCP` (precipitation)

### NASA MODIS — Earthdata Cloud
```
Collection:  MOD10A1 V061
Concept ID:  C2565093311-NSIDC_CPRD
Tile:        h09v04 (Pacific Northwest)
Resolution:  500m · Daily
```

CMR granule search is public. File download requires NASA Earthdata authentication via bearer token. Sinusoidal projection reprojected to WGS84 (EPSG:4326) via `rasterio`.

---

## Repository Structure

```
rainier-snowpack/
├── src/
│   ├── python/
│   │   ├── fetch_snotel.py       # Daily SWE/depth/temp from AWDB
│   │   ├── fetch_hourly.py       # 48-hour hourly temperature fetch
│   │   ├── fetch_modis.py        # NASA MODIS download + reproject + stats
│   │   ├── process_raster.py     # Raster processing utilities
│   │   └── build_dashboard.py    # Dashboard assembly
│   └── r/
│       └── snowpack_stats.R      # Basin statistics + ggplot2 charts
├── data/
│   ├── raw/modis/                # HDF4 granule files (gitignored)
│   └── processed/
│       ├── snotel_wy2026.csv     # Full water year time series
│       ├── snotel_latest.json    # Latest station snapshot
│       ├── basin_daily.csv       # Daily basin averages
│       ├── hourly_temps.csv      # 48-hour hourly temperature data
│       ├── hourly_temps.json     # Hourly data for dashboard
│       └── modis/
│           ├── modis_latest.json # Latest snow cover stats
│           └── snow_cover_*.tif  # Reprojected GeoTIFFs (gitignored)
├── dashboard/                    # Files served by GitHub Pages
│   ├── snotel_latest.json
│   ├── basin_daily.csv
│   ├── hourly_temps.json
│   ├── modis_latest.json
│   └── modis_snow_cover.png
├── outputs/                      # Generated charts
│   ├── basin_swe_timeseries.png
│   ├── all_stations_swe.png
│   ├── station_swe_bars.png
│   └── modis_snow_cover.png
├── .github/workflows/
│   └── daily_update.yml          # Hourly CI/CD pipeline
├── index.html                    # Live dashboard (GitHub Pages root)
└── pixi.toml                     # Environment + task runner
```

---

## Pipeline

GitHub Actions runs every hour at `:00` UTC:

```
fetch → fetch-hourly → fetch-modis → analyze → copy outputs → commit → deploy
```

```yaml
on:
  schedule:
    - cron: "0 * * * *"
  workflow_dispatch:        # also triggerable manually
```

Each run commits updated JSON and PNG files to `dashboard/`, which GitHub Pages serves automatically at `bdgroves.github.io/rainier-snowpack`.

---

## Local Setup

### Prerequisites
- [pixi](https://prefix.dev/docs/pixi/overview) — conda-based environment manager
- [NASA Earthdata account](https://urs.earthdata.nasa.gov/users/new) — free registration

### Install
```powershell
git clone git@github.com:bdgroves/rainier-snowpack.git
cd rainier-snowpack
pixi install
```

### NASA Earthdata credentials
Create `C:\Users\<you>\_netrc` (Windows) or `~/.netrc` (Linux/Mac):
```
machine urs.earthdata.nasa.gov
login YOUR_USERNAME
password YOUR_PASSWORD
```

Also generate a bearer token at **urs.earthdata.nasa.gov → My Profile → Generate Token** and set it as `EARTHDATA_TOKEN` in your environment for local runs.

### Run the full pipeline
```powershell
pixi run update
```

### Run individual steps
```powershell
pixi run fetch          # Daily SNOTEL SWE/depth/temp
pixi run fetch-hourly   # 48-hour temperature data
pixi run fetch-modis    # NASA MODIS satellite snow cover
pixi run analyze        # R statistics + charts
pixi run dashboard      # Build dashboard HTML
```

---

## GitHub Actions Secrets

| Secret | Description |
|---|---|
| `EARTHDATA_USERNAME` | NASA Earthdata username |
| `EARTHDATA_PASSWORD` | NASA Earthdata password |
| `EARTHDATA_TOKEN` | NASA Earthdata bearer token |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Data fetch | Python · `requests` · `httpx` |
| Satellite auth | NASA Earthdata bearer token |
| Raster processing | `rasterio` · `libgdal-hdf4` · sinusoidal → WGS84 |
| Statistics + charts | R · `tidyverse` · `zoo` · `ggplot2` |
| Satellite visualization | `matplotlib` · custom dark theme |
| Dashboard | Vanilla JS · Chart.js · CSS Grid |
| Environment | `pixi` (conda-forge) · Python 3.12 · R 4.5 |
| Automation | GitHub Actions |
| Hosting | GitHub Pages |

---

## MODIS Snow Cover Values

NDSI Snow Cover pixel encoding in MOD10A1:

| Value | Meaning |
|---|---|
| 0–100 | Snow cover % (0 = bare ground, 100 = full snow) |
| 200 | Missing data |
| 250 | Cloud obscured |
| 237 | Inland water |
| 255 | Fill / no data |

Rainier watershed clip: `(-122.5°W, 46.0°N, -121.0°W, 47.5°N)`

---

## Current Conditions — WY2026

As of early March 2026:

- **Basin avg SWE ~16–17"** with Morse Lake leading at 25.7"
- **6 of 7 stations freezing overnight** — refreeze after a warm February
- **72% satellite snow cover** — continuous snowpack from summit to mid-elevation
- **Avg NDSI 27.1** — moderate density, early-season melt cycling underway
- **Cougar Mountain (3,210 ft) nearly bare** — 0.4" SWE, low-elevation season over

---

## License

MIT — data from NRCS (public domain) and NASA Earthdata (open access).

---

*Built with Python · R · NASA Earthdata · NRCS SNOTEL · GitHub Actions*  
*46.8523°N 121.7269°W — 14,411 ft — Mt. Rainier, Washington*

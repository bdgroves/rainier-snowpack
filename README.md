# 🏔️ Mt. Rainier Snowpack Monitor

<div align="center">

![Mt. Rainier](https://img.shields.io/badge/Mt._Rainier-14%2C411_ft-4fc3f7?style=for-the-badge&logoColor=white)
![Status](https://img.shields.io/badge/Status-LIVE-69f0ae?style=for-the-badge)
![Updated](https://img.shields.io/badge/Updated-Hourly-81d4fa?style=for-the-badge)
![Stations](https://img.shields.io/badge/SNOTEL_Stations-7_Active-4fc3f7?style=for-the-badge)

### **[🌨️ VIEW LIVE DASHBOARD →](https://bdgroves.github.io/rainier-snowpack/)**

*Real-time snowpack conditions for Mt. Rainier and surrounding watersheds.*  
*Updated automatically every hour. No human required.*

</div>

---

## ❄️ What Is This?

Every hour, this pipeline wakes up and does something beautiful — it reaches out across the internet to 7 SNOTEL weather stations scattered across the flanks of Mt. Rainier, pulls down the latest snow water equivalent, snow depth, temperature, and precipitation readings, runs them through an R statistical analysis, rebuilds the dashboard, and publishes it all to the web. Automatically.

On top of daily snowpack data, the pipeline also fetches **48 hours of hourly temperature readings** from every station — capturing the full diurnal freeze/thaw cycle that drives snowmelt. When Bumping Ridge hits 53°F in the afternoon and drops to 27°F overnight, you see it in real time.

This is the kind of operational snowpack monitoring system used by water resource managers, avalanche forecasters, and hydrologists — built from scratch with open data and open source tools.

> *"The mountain doesn't care about your schedule. This pipeline does."*

---

## 🌐 Live Dashboard

**👉 [bdgroves.github.io/rainier-snowpack](https://bdgroves.github.io/rainier-snowpack/)**

The dashboard shows:
- **Basin average SWE** — snow water equivalent across all 7 stations
- **Full water year time series** — accumulation curve from Oct 1 through today
- **Per-station conditions** — SWE, snow depth, and temperature at each site
- **Elevation gradient** — how snowpack varies with altitude
- **Freeze/thaw status** — which stations are above and below 32°F right now
- **48-hour diurnal temperature chart** — hourly temp curves for all 7 stations with dashed 32°F freezing line
- **Stat chips** — 48hr low, 48hr high, current basin avg, average hours below freezing

---

## 📡 Data Sources

| Source | What We Get | Update Frequency |
|--------|------------|-----------------|
| [NRCS SNOTEL AWDB](https://wcc.sc.egov.usda.gov/awdbRestApi/) | SWE, snow depth, temperature, precipitation | Hourly |
| [NRCS SNOTEL AWDB](https://wcc.sc.egov.usda.gov/awdbRestApi/) | 48-hour hourly temperature readings | Hourly |
| [NASA MODIS MOD10A1](https://nsidc.org/data/mod10a1) | 500m snow cover extent raster | Daily *(coming soon)* |

### SNOTEL Stations

| Station | Triplet | Elevation | Status |
|---------|---------|-----------|--------|
| Corral Pass | 418:WA:SNTL | 5,810 ft | 🟢 Active |
| Morse Lake | 642:WA:SNTL | 5,400 ft | 🟢 Active |
| Cayuse Pass | 1085:WA:SNTL | 5,260 ft | 🟢 Active |
| Paradise | 679:WA:SNTL | 5,150 ft | 🟢 Active |
| Bumping Ridge | 375:WA:SNTL | 4,600 ft | 🟢 Active |
| Olallie Meadows | 672:WA:SNTL | 4,010 ft | 🟢 Active |
| Cougar Mountain | 420:WA:SNTL | 3,210 ft | 🟢 Active |

---

## 🛠️ Tech Stack

```
rainier-snowpack/
├── 🐍 Python 3.12        — NRCS API fetching, hourly temp pipeline, JSON/CSV output
├── 📊 R 4.5              — statistical analysis, ggplot2 visualization
├── 📦 pixi               — unified Python + R package manager
├── ⚙️  GitHub Actions     — hourly cron scheduler
└── 🌐 GitHub Pages       — free live hosting
```

### Why pixi?
[pixi](https://pixi.sh) manages both Python and R dependencies in a single `pixi.toml` file — no conda environments, no separate `renv`, no version conflicts. One command (`pixi install`) gets you a fully reproducible environment on any machine.

---

## 🚀 Run It Yourself

### Prerequisites
- [pixi](https://pixi.sh)
- Git

### Setup

```bash
git clone https://github.com/bdgroves/rainier-snowpack.git
cd rainier-snowpack
pixi install
```

### Run the full pipeline

```bash
pixi run fetch          # pull daily SNOTEL data
pixi run fetch-hourly   # pull 48hr hourly temperatures
pixi run analyze        # R analysis + generate plots
```

### View the dashboard locally

Open `index.html` in your browser — it reads from `data/processed/` automatically.

---

## ⚙️ How the Automation Works

```
Every hour on the dot
         │
         ▼
  GitHub Actions wakes up
         │
         ▼
  pixi run fetch
  ┌─────────────────────────────────────┐
  │  Calls NRCS AWDB REST API           │
  │  7 stations × 4 elements            │
  │  SWE, depth, temp, precip           │
  │  Full water year (Oct 1 → yesterday)│
  └─────────────────────────────────────┘
         │
         ▼
  pixi run fetch-hourly
  ┌─────────────────────────────────────┐
  │  48 hours of hourly TOBS readings   │
  │  All 7 stations                     │
  │  Captures full diurnal cycle        │
  │  Freeze/thaw hour counts            │
  └─────────────────────────────────────┘
         │
         ▼
  pixi run analyze
  ┌─────────────────────────────────────┐
  │  R reads snotel_wy2026.csv          │
  │  Computes basin averages            │
  │  Detects melt signals               │
  │  Generates 3 publication plots      │
  └─────────────────────────────────────┘
         │
         ▼
  Copy outputs → dashboard/
         │
         ▼
  git commit + push (only if data changed)
         │
         ▼
  🌐 Live dashboard updated
```

---

## 📈 What the Data Is Telling Us Right Now

As of early March 2026:

- **53.2°F** — 48-hour high at Bumping Ridge. Serious afternoon melt signal.
- **27.0°F** — 48-hour basin low. Overnight refreeze still holding the pack together.
- **26°F diurnal swing** — classic early melt season pattern. The mountain is waking up.
- **Morse Lake leads at 25.6" SWE** — despite not being the highest station, a microclimate sweet spot.
- **Cougar Mountain at 0.8"** — snow line is sitting well above 3,200 ft.

---

## 🗺️ Next Steps & Roadmap

### 🔜 Coming Soon
- [ ] **MODIS snow cover map** — NASA satellite raster showing actual snow extent clipped to the Rainier watershed
- [ ] **30-year historical median** — overlay the 1991–2020 normal line so you can see if this year is above or below average
- [ ] **Melt season alerts** — auto-flag when basin SWE drops >0.5"/day for 3 consecutive days
- [ ] **Hourly SWE tracking** — extend hourly fetcher beyond temperature to catch real-time accumulation events

### 🗺️ GIS Enhancements
- [ ] **QGIS project** — auto-generated `.qgz` with station points, watershed boundary, and snow raster pre-loaded
- [ ] **Folium interactive map** — clickable station map with popups showing latest readings
- [ ] **DEM-based snow line estimate** — derive current snow line elevation from MODIS + DEM
- [ ] **HUC-12 watershed delineation** — White River / Nisqually proper boundaries

### 📊 Analysis Upgrades
- [ ] **Mann-Kendall trend test** — is Rainier's snowpack declining over decades?
- [ ] **Peak SWE forecasting** — simple regression to predict this year's peak
- [ ] **Runoff correlation** — compare SWE with USGS streamflow on the White River
- [ ] **ENSO signal** — overlay El Niño / La Niña years to show climate teleconnections

### 🌦️ More Data
- [ ] **PRISM climate normals** — gridded precip and temp for the watershed
- [ ] **Crystal Mountain / White Pass** — expand station network
- [ ] **NPS Paradise webcam** — embed live summit camera feed
- [ ] **Northwest Avalanche Center** — link to daily avalanche forecast

---

## 🏔️ Why Rainier's Snowpack Matters

Mt. Rainier (14,411 ft / 4,392 m) is the highest peak in the Cascade Range and one of the most glaciated mountains in the contiguous United States. Its snowpack feeds:

- **Water supply** — the Puyallup, Nisqually, White, and Carbon rivers supply water to the greater Puget Sound region
- **Flood risk** — rapid snowmelt or rain-on-snow events can cause catastrophic downstream flooding
- **Ecology** — snowmelt timing controls stream temperatures, salmon habitat, and subalpine meadow phenology
- **Recreation** — Paradise averages 650+ inches of snowfall per year, one of the snowiest inhabited places on Earth

---

## 📄 License

MIT License — use it, fork it, build on it.

---

<div align="center">

Built with ❄️ by [bdgroves](https://github.com/bdgroves)

**[🌨️ Live Dashboard](https://bdgroves.github.io/rainier-snowpack/)** · **[NRCS SNOTEL](https://www.nrcs.usda.gov/wps/portal/wcc/home/)** · **[NASA Earthdata](https://earthdata.nasa.gov/)**

*The mountain is talking. Now you've got a system that listens.*

</div>

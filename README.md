# 🏔️ Mt. Rainier Snowpack Monitor

<div align="center">

![Mt. Rainier](https://img.shields.io/badge/Mt._Rainier-14%2C411_ft-4fc3f7?style=for-the-badge&logo=mountain&logoColor=white)
![Status](https://img.shields.io/badge/Status-LIVE-69f0ae?style=for-the-badge)
![Updated](https://img.shields.io/badge/Updated-Daily_8AM_Pacific-81d4fa?style=for-the-badge)
![Stations](https://img.shields.io/badge/SNOTEL_Stations-7_Active-4fc3f7?style=for-the-badge)

### **[🌨️ VIEW LIVE DASHBOARD →](https://bdgroves.github.io/rainier-snowpack/)**

*Real-time snowpack conditions for Mt. Rainier and surrounding watersheds.*
*Updated automatically every morning at 8AM Pacific.*

</div>

---

## ❄️ What Is This?

Every morning at 8AM, this pipeline wakes up and does something beautiful — it reaches out across the internet to 7 SNOTEL weather stations scattered across the flanks of Mt. Rainier, pulls down the latest snow water equivalent, snow depth, and temperature readings, runs them through an R statistical analysis, rebuilds the dashboard, and publishes it all to the web. Automatically. No human required.

This is the kind of operational snowpack monitoring system used by water resource managers, avalanche forecasters, and hydrologists — built from scratch with open data and open source tools.

> *"The mountains are calling and I must go."* — John Muir

---

## 🌐 Live Dashboard

**👉 [bdgroves.github.io/rainier-snowpack](https://bdgroves.github.io/rainier-snowpack/)**

The dashboard shows:
- **Basin average SWE** — snow water equivalent across all 7 stations
- **Full water year time series** — accumulation curve from Oct 1 through today
- **Per-station conditions** — SWE, snow depth, and temperature at each site
- **Elevation gradient** — how snowpack varies with altitude
- **Freeze/thaw status** — which stations are above and below 32°F right now

---

## 📡 Data Sources

| Source | What We Get | Update Frequency |
|--------|------------|-----------------|
| [NRCS SNOTEL AWDB](https://wcc.sc.egov.usda.gov/awdbRestApi/) | SWE, snow depth, temperature, precipitation | Daily |
| [NASA MODIS MOD10A1](https://nsidc.org/data/mod10a1) | 500m snow cover extent raster | Daily *(coming soon)* |
| [USGS StreamStats](https://streamstats.usgs.gov/) | Watershed boundary | Static |

### SNOTEL Stations

| Station | Triplet | Elevation | Status |
|---------|---------|-----------|--------|
| Morse Lake | 642:WA:SNTL | 5,400 ft | 🟢 Active |
| Corral Pass | 418:WA:SNTL | 5,810 ft | 🟢 Active |
| Cayuse Pass | 1085:WA:SNTL | 5,260 ft | 🟢 Active |
| Paradise | 679:WA:SNTL | 5,150 ft | 🟢 Active |
| Bumping Ridge | 375:WA:SNTL | 4,600 ft | 🟢 Active |
| Olallie Meadows | 672:WA:SNTL | 4,010 ft | 🟢 Active |
| Cougar Mountain | 420:WA:SNTL | 3,210 ft | 🟢 Active |

---

## 🛠️ Tech Stack

```
rainier-snowpack/
├── 🐍 Python 3.12        — data fetching, API calls, JSON/CSV output
├── 📊 R 4.5              — statistical analysis, ggplot2 visualization
├── 📦 pixi               — unified Python + R package manager
├── ⚙️  GitHub Actions     — daily automation at 8AM Pacific
└── 🌐 GitHub Pages       — free hosting for the live dashboard
```

### Why pixi?
[pixi](https://pixi.sh) manages both Python and R dependencies in a single `pixi.toml` file — no conda environments, no separate `renv`, no version conflicts. One command (`pixi install`) gets you a fully reproducible environment on any machine.

---

## 🚀 Run It Yourself

### Prerequisites
- [pixi](https://pixi.sh) — install with one command
- Git

### Setup

```bash
git clone https://github.com/bdgroves/rainier-snowpack.git
cd rainier-snowpack
pixi install
```

### Run the full pipeline

```bash
pixi run fetch      # pull SNOTEL data from NRCS
pixi run analyze    # R analysis + generate plots
```

### Or run everything at once

```bash
pixi run update
```

### View the dashboard locally

Open `dashboard/index.html` in your browser — it reads from `data/processed/snotel_latest.json` automatically.

---

## ⚙️ How the Automation Works

```
Every day at 8:00 AM Pacific (16:00 UTC)
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
  │  Full water year (Oct 1 → today)    │
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
  git commit + push
         │
         ▼
  GitHub Pages rebuilds
         │
         ▼
  🌐 Live dashboard updated
```

---

## 📈 Sample Output

The pipeline generates three plots daily:

**Basin SWE Time Series** — accumulation curve for the full water year

**All Stations SWE** — individual station curves showing elevation gradients and microclimate variation

**Station SWE Bar Chart** — ranked snapshot of current conditions by station

---

## 🗺️ Next Steps & Ideas

This project is a living system. Here's the roadmap:

### 🔜 Coming Soon
- [ ] **MODIS snow cover map** — add NASA satellite raster showing actual snow extent on the mountain, clipped to the Rainier watershed
- [ ] **30-year historical median** — overlay the 1991–2020 normal line on the SWE chart so you can instantly see if this year is above or below average
- [ ] **Melt season alerts** — GitHub Issue auto-created when basin SWE drops >0.5"/day for 3 consecutive days

### 🗺️ GIS Enhancements
- [ ] **QGIS project** — auto-generated `.qgz` with station points, watershed boundary, and latest snow raster pre-loaded
- [ ] **Folium interactive map** — clickable station map with popups showing latest readings
- [ ] **DEM-based snow line** — estimate current snow line elevation from MODIS + DEM
- [ ] **Watershed delineation** — proper HUC-12 boundary for the White River / Nisqually watersheds

### 📊 Analysis Upgrades
- [ ] **Mann-Kendall trend test** — is Rainier's snowpack declining over decades?
- [ ] **Peak SWE forecasting** — simple regression model to predict this year's peak
- [ ] **Runoff correlation** — compare SWE with USGS streamflow on the White River
- [ ] **ENSO signal** — overlay El Niño / La Niña years to show climate teleconnections

### 🌦️ More Data
- [ ] **PRISM climate normals** — gridded precip and temp for the watershed
- [ ] **Snotel network expansion** — add Crystal Mountain, White Pass stations
- [ ] **Webcam integration** — embed NPS Paradise webcam feed
- [ ] **Avalanche conditions** — link to Northwest Avalanche Center forecast

### 🖥️ Dashboard Improvements
- [ ] **Date picker** — browse any date in the historical record
- [ ] **Station detail pages** — click a station for its full history
- [ ] **Mobile-optimized layout**
- [ ] **Dark/light mode toggle**

---

## 🏔️ About Mt. Rainier

Mt. Rainier (14,411 ft / 4,392 m) is the highest peak in the Cascade Range and one of the most glaciated mountains in the contiguous United States. Its snowpack is critical to:

- **Water supply** — the Puyallup, Nisqually, White, and Carbon rivers all originate here, supplying water to the Puget Sound region
- **Flood risk** — rapid snowmelt or rain-on-snow events can cause catastrophic flooding downstream
- **Ecology** — snowmelt timing controls stream temperatures, salmon habitat, and subalpine meadow phenology
- **Recreation** — Paradise averages 650+ inches of snowfall per year, one of the snowiest places on Earth

---

## 📄 License

MIT License — use it, fork it, build on it.

---

<div align="center">

Built with ❄️ by [bdgroves](https://github.com/bdgroves)

**[🌨️ Live Dashboard](https://bdgroves.github.io/rainier-snowpack/)** · **[NRCS SNOTEL](https://www.nrcs.usda.gov/wps/portal/wcc/home/)** · **[NASA Earthdata](https://earthdata.nasa.gov/)**

*The mountain doesn't care about your schedule. This pipeline does.*

</div>

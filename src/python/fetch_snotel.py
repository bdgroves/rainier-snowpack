"""
fetch_snotel.py
Fetches daily SNOTEL data from NRCS AWDB REST API for 7 stations
surrounding Mt. Rainier. Saves full water year CSV and a latest JSON
snapshot including computed fields:
  - 24hr SWE change (new snow / melt signal)
  - Snow density % (SWE / depth)
  - Days since last measurable snowfall
  - Melt rate alert flag
"""

import json
import logging
import requests
import pandas as pd
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
LOG = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
STATIONS = [
    {"id": "679:WA:SNTL",  "name": "Paradise",        "elevation": 5150},
    {"id": "642:WA:SNTL",  "name": "Morse Lake",       "elevation": 5400},
    {"id": "672:WA:SNTL",  "name": "Olallie Meadows",  "elevation": 4010},
    {"id": "1085:WA:SNTL", "name": "Cayuse Pass",      "elevation": 5260},
    {"id": "418:WA:SNTL",  "name": "Corral Pass",      "elevation": 5810},
    {"id": "375:WA:SNTL",  "name": "Bumping Ridge",    "elevation": 4600},
    {"id": "420:WA:SNTL",  "name": "Cougar Mountain",  "elevation": 3210},
]

ELEMENTS    = ["WTEQ", "SNWD", "TOBS", "PRCP"]
BASE_URL    = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
PROC_DIR    = Path("data/processed")
WATER_YEAR  = 2026

# Thresholds
MELT_ALERT_THRESHOLD   = 0.5   # inches SWE lost in 24hrs triggers alert
NEW_SNOW_MIN           = 0.1   # inches SWE gain counts as new snowfall


def water_year_start(wy: int) -> date:
    return date(wy - 1, 10, 1)


def fetch_station(station_id: str, start: date, end: date) -> dict:
    """Fetch all elements for one station, return dict keyed by element."""
    results = {}
    for element in ELEMENTS:
        try:
            r = requests.get(
                BASE_URL,
                params={
                    "stationTriplets": station_id,
                    "elementCd":       element,
                    "beginDate":       str(start),
                    "endDate":         str(end),
                    "periodRef":       "START",
                    "duration":        "DAILY",
                    "getFlags":        "false",
                    "unitSystem":      "ENGLISH",
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if data and data[0].get("values"):
                results[element] = data[0]["values"]
            else:
                results[element] = []
        except Exception as e:
            LOG.warning("%s failed for %s: %s", element, station_id, e)
            results[element] = []
    return results


def build_series(raw: dict, element: str, start: date, n_days: int) -> list:
    """Align element values to date range, filling gaps with None."""
    values = raw.get(element, [])
    series = [None] * n_days
    for i, v in enumerate(values):
        if i < n_days:
            try:
                series[i] = float(v) if v not in (None, "", "NA", -99.9, -9999) else None
            except (ValueError, TypeError):
                series[i] = None
    return series


def compute_snow_metrics(swe_series: list, prcp_series: list, dates: list) -> dict:
    """
    Compute derived snow metrics from SWE and PRCP time series.
    Returns dict with:
      - swe_change_24h: SWE difference from yesterday to today
      - days_since_snow: days since last measurable new snowfall
      - melt_alert: True if SWE dropped > threshold in last 24hrs
    """
    # Get last two valid SWE readings
    valid_swe = [(i, v) for i, v in enumerate(swe_series) if v is not None]

    swe_change_24h = None
    melt_alert     = False

    if len(valid_swe) >= 2:
        today_swe = valid_swe[-1][1]
        prev_swe  = valid_swe[-2][1]
        swe_change_24h = round(today_swe - prev_swe, 2)
        if swe_change_24h < -MELT_ALERT_THRESHOLD:
            melt_alert = True

    # Days since last measurable snowfall
    # Use PRCP (incremental precip) as proxy for new snow
    # Walk backwards from most recent date
    days_since_snow = None
    today_idx = len(dates) - 1

    # Try PRCP series first
    valid_prcp = [(i, v) for i, v in enumerate(prcp_series) if v is not None]
    if valid_prcp:
        for i, v in reversed(valid_prcp):
            if v >= NEW_SNOW_MIN:
                days_since_snow = today_idx - i
                break

    # Fallback: use positive SWE increments if PRCP unavailable
    if days_since_snow is None and len(valid_swe) >= 2:
        for j in range(len(valid_swe) - 1, 0, -1):
            curr_i, curr_v = valid_swe[j]
            prev_i, prev_v = valid_swe[j - 1]
            if curr_v - prev_v >= NEW_SNOW_MIN:
                days_since_snow = today_idx - curr_i
                break

    return {
        "swe_change_24h": swe_change_24h,
        "days_since_snow": days_since_snow,
        "melt_alert": melt_alert,
    }


def main():
    LOG.info("=== SNOTEL fetch — Water Year %d ===", WATER_YEAR)

    today     = date.today() - timedelta(days=1)   # confirmed through yesterday
    start     = water_year_start(WATER_YEAR)
    n_days    = (today - start).days + 1
    dates     = [str(start + timedelta(days=i)) for i in range(n_days)]

    LOG.info("Pipeline run: %s UTC", date.today())
    LOG.info("Date range: %s → %s (%d days confirmed through yesterday)", start, today, n_days)

    PROC_DIR.mkdir(parents=True, exist_ok=True)

    all_rows    = []
    latest_list = []
    basin_swe_series = [0.0] * n_days
    basin_swe_count  = [0]   * n_days

    for stn in STATIONS:
        LOG.info("Fetching %s (%s)...", stn["name"], stn["id"])
        raw = fetch_station(stn["id"], start, today)

        swe_s   = build_series(raw, "WTEQ", start, n_days)
        depth_s = build_series(raw, "SNWD", start, n_days)
        temp_s  = build_series(raw, "TOBS", start, n_days)
        prcp_s  = build_series(raw, "PRCP", start, n_days)

        # Accumulate basin average
        for i, v in enumerate(swe_s):
            if v is not None:
                basin_swe_series[i] += v
                basin_swe_count[i]  += 1

        # Build full CSV rows
        for i, d in enumerate(dates):
            all_rows.append({
                "date":       d,
                "station_id": stn["id"],
                "name":       stn["name"],
                "elevation":  stn["elevation"],
                "swe_in":     swe_s[i],
                "depth_in":   depth_s[i],
                "temp_f":     temp_s[i],
                "prcp_in":    prcp_s[i],
            })

        # Latest snapshot + derived metrics
        latest_swe   = next((v for v in reversed(swe_s)   if v is not None), None)
        latest_depth = next((v for v in reversed(depth_s) if v is not None), None)
        latest_temp  = next((v for v in reversed(temp_s)  if v is not None), None)

        metrics = compute_snow_metrics(swe_s, prcp_s, dates)

        # Snow density (%)
        density = None
        if latest_swe is not None and latest_depth and latest_depth > 0:
            density = round(latest_swe / (latest_depth / 12) * 100, 1)

        latest_list.append({
            "id":              stn["id"],
            "name":            stn["name"],
            "elevation":       stn["elevation"],
            "swe_in":          latest_swe,
            "depth_in":        latest_depth,
            "temp_f":          latest_temp,
            "swe_change_24h":  metrics["swe_change_24h"],
            "days_since_snow": metrics["days_since_snow"],
            "melt_alert":      metrics["melt_alert"],
            "density_pct":     density,
        })

        status = "✓" if latest_swe is not None else "✗"
        change_str = f"{metrics['swe_change_24h']:+.2f}\"" if metrics["swe_change_24h"] is not None else "—"
        LOG.info("  %s SWE=%.1f\" change=%s days_snow=%s melt_alert=%s",
                 status,
                 latest_swe or 0,
                 change_str,
                 metrics["days_since_snow"],
                 metrics["melt_alert"])

    # ── Basin daily CSV ───────────────────────────────────────────────────────
    df = pd.DataFrame(all_rows)
    df.to_csv(PROC_DIR / "snotel_wy2026.csv", index=False)
    LOG.info("Saved snotel_wy2026.csv (%d rows)", len(df))

    basin_daily = []
    for i, d in enumerate(dates):
        c = basin_swe_count[i]
        basin_daily.append({
            "date":      d,
            "basin_swe": round(basin_swe_series[i] / c, 2) if c > 0 else None,
            "n_stations": c,
        })

    bd = pd.DataFrame(basin_daily)
    bd.to_csv(PROC_DIR / "basin_daily.csv", index=False)
    LOG.info("Saved basin_daily.csv (%d rows)", len(bd))

    # ── Basin-level derived metrics ───────────────────────────────────────────
    valid_changes = [s["swe_change_24h"] for s in latest_list if s["swe_change_24h"] is not None]
    valid_days    = [s["days_since_snow"] for s in latest_list if s["days_since_snow"] is not None]
    valid_density = [s["density_pct"]     for s in latest_list if s["density_pct"]     is not None]

    basin_change_24h  = round(sum(valid_changes) / len(valid_changes), 2) if valid_changes else None
    basin_days_snow   = round(sum(valid_days)    / len(valid_days),    0) if valid_days    else None
    basin_density     = round(sum(valid_density) / len(valid_density), 1) if valid_density else None
    any_melt_alert    = any(s["melt_alert"] for s in latest_list)

    # ── snotel_latest.json ────────────────────────────────────────────────────
    snapshot = {
        "updated":    str(date.today()),
        "water_year": WATER_YEAR,
        "basin": {
            "swe_change_24h":  basin_change_24h,
            "days_since_snow": int(basin_days_snow) if basin_days_snow is not None else None,
            "melt_alert":      any_melt_alert,
            "density_pct":     basin_density,
        },
        "stations": latest_list,
    }

    (PROC_DIR / "snotel_latest.json").write_text(json.dumps(snapshot, indent=2))
    LOG.info("Saved snotel_latest.json")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "="*55)
    print(f"  SNOTEL Snapshot — {today}")
    print("="*55)
    valid = [s for s in latest_list if s["swe_in"] is not None]
    if valid:
        avg_swe = sum(s["swe_in"] for s in valid) / len(valid)
        print(f"  Basin avg SWE:    {avg_swe:.1f}\"")
        print(f"  24hr SWE change:  {basin_change_24h:+.2f}\"" if basin_change_24h is not None else "  24hr SWE change:  —")
        print(f"  Days since snow:  {int(basin_days_snow) if basin_days_snow is not None else '—'}")
        print(f"  Melt alert:       {'🔴 YES' if any_melt_alert else '✓ No'}")
        print(f"  Snow density:     {basin_density}%" if basin_density else "  Snow density:     —")
    print("="*55)

    LOG.info("=== Done! ===")


if __name__ == "__main__":
    main()

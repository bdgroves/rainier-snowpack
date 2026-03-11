"""
fetch_snotel.py
Fetches daily SNOTEL data from NRCS AWDB REST API for 7 stations
surrounding Mt. Rainier. Uses httpx with 'elements' parameter.

Response format: data[0]["data"][0]["values"] = [{"date": "...", "value": 23.7}, ...]
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
LOG = logging.getLogger(__name__)

STATIONS = [
    {"id": "679:WA:SNTL",  "name": "Paradise",        "elevation_ft": 5150},
    {"id": "642:WA:SNTL",  "name": "Morse Lake",       "elevation_ft": 5400},
    {"id": "672:WA:SNTL",  "name": "Olallie Meadows",  "elevation_ft": 4010},
    {"id": "1085:WA:SNTL", "name": "Cayuse Pass",      "elevation_ft": 5260},
    {"id": "418:WA:SNTL",  "name": "Corral Pass",      "elevation_ft": 5810},
    {"id": "375:WA:SNTL",  "name": "Bumping Ridge",    "elevation_ft": 4600},
    {"id": "420:WA:SNTL",  "name": "Cougar Mountain",  "elevation_ft": 3210},
]

BASE_URL   = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
PROC_DIR   = Path("data/processed")
WATER_YEAR = 2026

MELT_ALERT_THRESHOLD = 0.5
NEW_SNOW_MIN         = 0.1


def water_year_start(wy: int) -> date:
    return date(wy - 1, 10, 1)


def fetch_element(client, station_id: str, element: str, start: date, end: date) -> dict:
    """
    Returns a dict of {date_str: float} for the requested element.
    Response format: [{"data": [{"values": [{"date": "...", "value": N}, ...]}]}]
    """
    params = {
        "stationTriplets": station_id,
        "elements":        element,
        "beginDate":       str(start),
        "endDate":         str(end),
        "duration":        "DAILY",
    }
    try:
        r = client.get(BASE_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        values = data[0]["data"][0]["values"]
        result = {}
        for v in values:
            try:
                val = v.get("value")
                if val is not None and val not in (-99.9, -9999):
                    result[v["date"]] = float(val)
            except (TypeError, ValueError):
                pass
        return result
    except Exception as e:
        LOG.warning("%s failed for %s: %s", element, station_id, e)
        return {}


def build_series(value_dict: dict, dates: list) -> list:
    """Align date-keyed values to ordered date list, filling gaps with None."""
    return [value_dict.get(d) for d in dates]


def compute_snow_metrics(swe_series: list, depth_series: list, prcp_series: list, n_days: int) -> dict:
    valid_swe   = [(i, v) for i, v in enumerate(swe_series)   if v is not None]
    valid_depth = [(i, v) for i, v in enumerate(depth_series) if v is not None]

    swe_change_24h   = None
    depth_change_24h = None
    melt_alert       = False

    if len(valid_swe) >= 2:
        swe_change_24h = round(valid_swe[-1][1] - valid_swe[-2][1], 2)
        if swe_change_24h < -MELT_ALERT_THRESHOLD:
            melt_alert = True

    if len(valid_depth) >= 2:
        depth_change_24h = round(valid_depth[-1][1] - valid_depth[-2][1], 1)

    days_since_snow = None
    today_idx = n_days - 1
    valid_prcp = [(i, v) for i, v in enumerate(prcp_series) if v is not None]
    if valid_prcp:
        for i, v in reversed(valid_prcp):
            if v >= NEW_SNOW_MIN:
                days_since_snow = today_idx - i
                break

    if days_since_snow is None and len(valid_swe) >= 2:
        for j in range(len(valid_swe) - 1, 0, -1):
            curr_i, curr_v = valid_swe[j]
            prev_i, prev_v = valid_swe[j - 1]
            if curr_v - prev_v >= NEW_SNOW_MIN:
                days_since_snow = today_idx - curr_i
                break

    return {
        "swe_change_24h":   swe_change_24h,
        "depth_change_24h": depth_change_24h,
        "days_since_snow":  days_since_snow,
        "melt_alert":       melt_alert,
    }


def main():
    LOG.info("=== SNOTEL fetch — Water Year %d ===", WATER_YEAR)

    today  = date.today() - timedelta(days=1)
    start  = water_year_start(WATER_YEAR)
    n_days = (today - start).days + 1
    dates  = [str(start + timedelta(days=i)) for i in range(n_days)]

    LOG.info("Date range: %s → %s (%d days)", start, today, n_days)
    PROC_DIR.mkdir(parents=True, exist_ok=True)

    all_rows         = []
    latest_list      = []
    basin_swe_series = [0.0] * n_days
    basin_swe_count  = [0]   * n_days

    with httpx.Client() as client:
        for stn in STATIONS:
            LOG.info("Fetching %s (%s)...", stn["name"], stn["id"])

            swe_d   = fetch_element(client, stn["id"], "WTEQ", start, today)
            depth_d = fetch_element(client, stn["id"], "SNWD", start, today)
            temp_d  = fetch_element(client, stn["id"], "TOBS", start, today)
            prcp_d  = fetch_element(client, stn["id"], "PRCP", start, today)

            swe_s   = build_series(swe_d,   dates)
            depth_s = build_series(depth_d, dates)
            temp_s  = build_series(temp_d,  dates)
            prcp_s  = build_series(prcp_d,  dates)

            for i, v in enumerate(swe_s):
                if v is not None:
                    basin_swe_series[i] += v
                    basin_swe_count[i]  += 1

            for i, d in enumerate(dates):
                all_rows.append({
                    "date":            d,
                    "station_triplet": stn["id"],
                    "station_name":    stn["name"],
                    "elevation_ft":    stn["elevation_ft"],
                    "swe_in":          swe_s[i],
                    "depth_in":        depth_s[i],
                    "temp_f":          temp_s[i],
                    "precip_in":       prcp_s[i],
                })

            latest_swe   = next((v for v in reversed(swe_s)   if v is not None), None)
            latest_depth = next((v for v in reversed(depth_s) if v is not None), None)
            latest_temp  = next((v for v in reversed(temp_s)  if v is not None), None)
            metrics      = compute_snow_metrics(swe_s, depth_s, prcp_s, n_days)

            density = None
            if latest_swe is not None and latest_depth and latest_depth > 0:
                density = round((latest_swe / latest_depth) * 100, 1)

            latest_list.append({
                "id":               stn["id"],
                "name":             stn["name"],
                "elevation":        stn["elevation_ft"],
                "swe_in":           latest_swe,
                "depth_in":         latest_depth,
                "temp_f":           latest_temp,
                "swe_change_24h":   metrics["swe_change_24h"],
                "depth_change_24h": metrics["depth_change_24h"],
                "days_since_snow":  metrics["days_since_snow"],
                "melt_alert":       metrics["melt_alert"],
                "density_pct":      density,
            })

            status     = "✓" if latest_swe is not None else "✗"
            change_str = f"{metrics['swe_change_24h']:+.2f}\"" if metrics["swe_change_24h"] is not None else "—"
            LOG.info("  %s SWE=%.1f\" change=%s days_snow=%s",
                     status, latest_swe or 0, change_str, metrics["days_since_snow"])

    # ── Guard: never overwrite good data with empty results ───────────────────
    valid_stations = [s for s in latest_list if s["swe_in"] is not None]
    if not valid_stations:
        LOG.error("All stations returned no data — preserving existing snotel_latest.json")
        raise SystemExit(1)

    LOG.info("%d/%d stations with valid SWE", len(valid_stations), len(STATIONS))

    # ── CSVs ──────────────────────────────────────────────────────────────────
    pd.DataFrame(all_rows).to_csv(PROC_DIR / "snotel_wy2026.csv", index=False)
    LOG.info("Saved snotel_wy2026.csv (%d rows)", len(all_rows))

    basin_daily = [
        {"date": d, "basin_swe": round(basin_swe_series[i] / basin_swe_count[i], 2)
                                  if basin_swe_count[i] > 0 else None,
         "n_stations": basin_swe_count[i]}
        for i, d in enumerate(dates)
    ]
    pd.DataFrame(basin_daily).to_csv(PROC_DIR / "basin_daily.csv", index=False)
    LOG.info("Saved basin_daily.csv (%d rows)", len(basin_daily))

    # ── Basin metrics ─────────────────────────────────────────────────────────
    valid_changes       = [s["swe_change_24h"]   for s in valid_stations if s["swe_change_24h"]   is not None]
    valid_depth_changes = [s["depth_change_24h"] for s in valid_stations if s["depth_change_24h"] is not None]
    valid_days          = [s["days_since_snow"]  for s in valid_stations if s["days_since_snow"]  is not None]
    valid_density       = [s["density_pct"]      for s in valid_stations if s["density_pct"]      is not None]

    basin_change       = round(sum(valid_changes) / len(valid_changes), 2)             if valid_changes       else None
    basin_depth_change = round(sum(valid_depth_changes) / len(valid_depth_changes), 1) if valid_depth_changes else None
    basin_days         = int(round(sum(valid_days) / len(valid_days)))                 if valid_days          else None
    basin_density      = round(sum(valid_density) / len(valid_density), 1)             if valid_density       else None
    any_melt           = any(s["melt_alert"] for s in valid_stations)

    (PROC_DIR / "snotel_latest.json").write_text(json.dumps({
        "updated":    str(date.today()),
        "water_year": WATER_YEAR,
        "basin": {
            "swe_change_24h":   basin_change,
            "depth_change_24h": basin_depth_change,
            "days_since_snow":  basin_days,
            "melt_alert":       any_melt,
            "density_pct":      basin_density,
        },
        "stations": latest_list,
    }, indent=2))
    LOG.info("Saved snotel_latest.json")

    avg_swe = sum(s["swe_in"] for s in valid_stations) / len(valid_stations)
    print(f"\n{'='*55}")
    print(f"  SNOTEL Snapshot — {today}")
    print(f"{'='*55}")
    print(f"  Basin avg SWE:    {avg_swe:.1f}\"")
    print(f"  24hr SWE change:  {basin_change:+.2f}\"" if basin_change is not None else "  24hr SWE change:  —")
    print(f"  24hr depth chg:   {basin_depth_change:+.1f}\"" if basin_depth_change is not None else "  24hr depth chg:   —")
    print(f"  Days since snow:  {basin_days if basin_days is not None else '—'}")
    print(f"  Melt alert:       {'🔴 YES' if any_melt else '✓ No'}")
    print(f"  Snow density:     {basin_density}%" if basin_density else "  Snow density:     —")
    print(f"{'='*55}")
    LOG.info("=== Done! ===")


if __name__ == "__main__":
    main()

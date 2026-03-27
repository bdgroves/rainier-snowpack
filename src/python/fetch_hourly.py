"""
fetch_hourly.py
Fetches 48 hours of hourly temperature data from NRCS SNOTEL
for all Mt. Rainier stations. Saves CSV + JSON for dashboard charting.
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
LOG = logging.getLogger(__name__)

STATIONS = [
    ("679:WA:SNTL",  "Paradise",        5150),
    ("642:WA:SNTL",  "Morse Lake",      5400),
    ("672:WA:SNTL",  "Olallie Meadows", 4010),
    ("1085:WA:SNTL", "Cayuse Pass",     5260),
    ("418:WA:SNTL",  "Corral Pass",     5810),
    ("375:WA:SNTL",  "Bumping Ridge",   4600),
    ("420:WA:SNTL",  "Cougar Mountain", 3210),
]

OUT_DIR  = Path("data/processed")
RAW_DIR  = Path("data/raw/snotel")
BASE_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"


def fetch_station_hourly(client, triplet, name, elev_ft, start, end):
    LOG.info("Fetching hourly temps: %s...", name)
    params = {
        "stationTriplets": triplet,
        "elements":        "TOBS",
        "beginDate":       str(start),
        "endDate":         str(end),
        "duration":        "HOURLY",
    }
    try:
        r = client.get(BASE_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        LOG.error("  Failed %s: %s", name, e)
        return None

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{triplet.replace(':','_')}_TOBS_hourly_{end}.json").write_text(
        json.dumps(data, indent=2)
    )

    if not data or not data[0].get("data"):
        LOG.warning("  No data for %s", name)
        return None

    values = data[0]["data"][0].get("values", [])
    if not values:
        return None

    df = pd.DataFrame(values)
    df["datetime"]     = pd.to_datetime(df["date"])
    df["temp_f"]       = pd.to_numeric(df["value"], errors="coerce")
    df["station_name"] = name
    df["elevation_ft"] = elev_ft
    df["triplet"]      = triplet
    df["freezing"]     = df["temp_f"] <= 32.0

    clean = df["temp_f"].dropna()
    if not clean.empty:
        LOG.info("  %d readings | latest: %.1f F | min: %.1f F | max: %.1f F",
                 len(df), clean.iloc[-1], clean.min(), clean.max())

    return df[["datetime", "temp_f", "freezing", "station_name", "elevation_ft", "triplet"]]


def main():
    today = date.today()
    start = today - timedelta(days=2)
    end   = today + timedelta(days=1)  # fetch through "tomorrow" to get all of today's readings

    LOG.info("=== Hourly temp fetch: %s to %s ===", start, today)

    frames = []
    with httpx.Client(follow_redirects=True, verify=False) as client:
        for triplet, name, elev in STATIONS:
            df = fetch_station_hourly(client, triplet, name, elev, start, end)
            if df is not None:
                frames.append(df)

    if not frames:
        LOG.error("No data!")
        raise SystemExit(1)

    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values(["station_name", "datetime"], inplace=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUT_DIR / "hourly_temps.csv", index=False)

    # Build JSON for dashboard charts
    chart_data = {}
    for station in combined["station_name"].unique():
        sdf = combined[combined["station_name"] == station].sort_values("datetime")
        clean = sdf.dropna(subset=["temp_f"])
        chart_data[station] = {
            "elevation": int(sdf["elevation_ft"].iloc[0]),
            "labels":    sdf["datetime"].dt.strftime("%m/%d %H:%M").tolist(),
            "temps":     sdf["temp_f"].where(sdf["temp_f"].notna(), None).round(1).tolist(),
            "freezing":  sdf["freezing"].tolist(),
            "min_temp":  round(float(clean["temp_f"].min()), 1) if not clean.empty else None,
            "max_temp":  round(float(clean["temp_f"].max()), 1) if not clean.empty else None,
            "latest":    round(float(clean["temp_f"].iloc[-1]), 1) if not clean.empty else None,
        }

    (OUT_DIR / "hourly_temps.json").write_text(
        json.dumps(chart_data, indent=2, default=str)
    )

    # Summary table
    print("\n" + "="*70)
    print(f"  48-Hour Temperature Summary (through {today})")
    print("="*70)
    summary = combined.groupby("station_name").agg(
        elev_ft     =("elevation_ft", "first"),
        latest_f    =("temp_f", "last"),
        min_f       =("temp_f", "min"),
        max_f       =("temp_f", "max"),
        hrs_freezing=("freezing", "sum"),
    ).sort_values("elev_ft", ascending=False)
    print(summary.to_string())
    print("="*70)

    LOG.info("=== Done! %d/%d stations ===", len(frames), len(STATIONS))


if __name__ == "__main__":
    main()

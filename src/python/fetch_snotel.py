"""
fetch_snotel.py
Fetches daily SWE, snow depth, and temperature from NRCS SNOTEL
for stations near Mt. Rainier using the AWDB REST API.
No authentication required.
"""

import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
LOG = logging.getLogger(__name__)

# ── SNOTEL stations near Mt. Rainier ─────────────────────────────────────────
STATIONS = [
    ("679:WA:SNTL",  "Paradise",       5150),
    ("642:WA:SNTL",  "Morse Lake",     5400),
    ("672:WA:SNTL",  "Olallie Meadows",4010),
    ("1085:WA:SNTL", "Cayuse Pass",    5260),
    ("418:WA:SNTL",  "Corral Pass",    5810),
    ("375:WA:SNTL",  "Bumping Ridge",  4600),
    ("420:WA:SNTL",  "Cougar Mountain",3210),
]

ELEMENTS = ["WTEQ", "SNWD", "TOBS", "PRCP"]

OUT_DIR  = Path("data/processed")
RAW_DIR  = Path("data/raw/snotel")
BASE_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"


def water_year_start() -> date:
    today = date.today()
    year = today.year if today.month >= 10 else today.year - 1
    return date(year, 10, 1)


def fetch_station(client, triplet, name, elev_ft, start, end) -> pd.DataFrame | None:
    """Fetch all elements for a single station."""
    LOG.info("Fetching %s (%s)...", name, triplet)

    dfs = []
    for element in ELEMENTS:
        params = {
            "stationTriplets": triplet,
            "elements":        element,
            "beginDate":       str(start),
            "endDate":         str(end),
            "duration":        "DAILY",
        }
        try:
            r = client.get(BASE_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            LOG.warning("  %s failed for %s: %s", element, name, e)
            continue

        # Save raw
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"{triplet.replace(':','_')}_{element}_{end}.json").write_text(
            json.dumps(data, indent=2)
        )

        # Parse
        if not data or not data[0].get("data"):
            LOG.warning("  No %s data for %s", element, name)
            continue

        values = data[0]["data"][0].get("values", [])
        if not values:
            continue

        df = pd.DataFrame(values)
        df["date"] = pd.to_datetime(df["date"])
        df.rename(columns={"value": element.lower()}, inplace=True)
        dfs.append(df.set_index("date"))

    if not dfs:
        LOG.warning("  No data at all for %s", name)
        return None

    # Merge all elements
    combined = pd.concat(dfs, axis=1).reset_index()
    combined["station_triplet"] = triplet
    combined["station_name"]    = name
    combined["elevation_ft"]    = elev_ft

    # Friendly column names
    combined.rename(columns={
        "wteq": "swe_in",
        "snwd": "depth_in",
        "tobs": "temp_f",
        "prcp": "precip_in",
    }, inplace=True)

    LOG.info("  ✓ %d rows | SWE latest: %s in", len(combined),
             combined["swe_in"].dropna().iloc[-1] if "swe_in" in combined.columns else "n/a")
    return combined


def main():
    today = date.today()
    start = water_year_start()
    wy    = start.year + 1

    LOG.info("=== SNOTEL fetch — Water Year %d ===", wy)
    LOG.info("Date range: %s → %s", start, today)

    frames = []
    with httpx.Client(follow_redirects=True) as client:
        for triplet, name, elev in STATIONS:
            df = fetch_station(client, triplet, name, elev, start, today)
            if df is not None:
                frames.append(df)

    if not frames:
        LOG.error("No data fetched!")
        raise SystemExit(1)

    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values(["station_triplet", "date"], inplace=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Full water-year CSV
    wy_path = OUT_DIR / f"snotel_wy{wy}.csv"
    combined.to_csv(wy_path, index=False)
    LOG.info("Saved: %s (%d rows, %d stations)", wy_path, len(combined), len(frames))

    # Latest snapshot
    latest = combined.groupby("station_triplet").last().reset_index()
    latest.to_csv(OUT_DIR / "snotel_latest.csv", index=False)

    # JSON for dashboard
    summary = {"updated": today.isoformat(), "water_year": wy, "stations": []}
    for _, row in latest.iterrows():
        summary["stations"].append({
            "id":        row["station_triplet"],
            "name":      row["station_name"],
            "elevation": int(row["elevation_ft"]),
            "swe_in":    row.get("swe_in"),
            "depth_in":  row.get("depth_in"),
            "temp_f":    row.get("temp_f"),
        })

    (OUT_DIR / "snotel_latest.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )

    # Print summary table
    print("\n" + "="*65)
    print(f"  Mt. Rainier SNOTEL — {today}  (WY{wy})")
    print("="*65)
    cols = ["station_name", "elevation_ft", "swe_in", "depth_in", "temp_f"]
    available = [c for c in cols if c in latest.columns]
    print(latest[available].to_string(index=False))
    print("="*65)

    LOG.info("=== Done! %d/%d stations ===", len(frames), len(STATIONS))


if __name__ == "__main__":
    main()

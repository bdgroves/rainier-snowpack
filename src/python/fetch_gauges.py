"""
fetch_gauges.py
Fetches real-time and 7-day historical streamflow (cfs) from USGS NWIS
for 4 rivers draining Mt. Rainier. Saves gauges_latest.json for the dashboard.

Gauges:
  Nisqually River  near National, WA     12082500
  Puyallup River   near Orting, WA       12093500
  Carbon River     near Orting, WA       12094300
  White River      near Buckley, WA      12098500
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
LOG = logging.getLogger(__name__)

GAUGES = [
    {"id": "12082500", "name": "Nisqually River", "location": "nr National"},
    {"id": "12083000", "name": "Mineral Creek",   "location": "nr Mineral"},
    {"id": "12101500", "name": "Puyallup River",  "location": "at Puyallup"},
    {"id": "12099200", "name": "White River",     "location": "ab Boise Cr, Buckley"},
]

# USGS NWIS instantaneous values API
NWIS_URL  = "https://waterservices.usgs.gov/nwis/iv/"
PROC_DIR  = Path("data/processed")
DASH_DIR  = Path("dashboard")

# Flood stage thresholds (cfs) — approximate, for alert coloring
FLOOD_CFS = {
    "12082500": 8000,   # Nisqually nr National
    "12083000": 3000,   # Mineral Creek nr Mineral
    "12101500": 25000,  # Puyallup at Puyallup
    "12099200": 8000,   # White River ab Boise Cr
}


def fetch_gauge(client, gauge: dict, days_back: int = 7) -> dict:
    """Fetch instantaneous discharge for a gauge, last N days."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    params = {
        "sites":         gauge["id"],
        "parameterCd":   "00060",          # discharge, cfs
        "startDT":       start.strftime("%Y-%m-%dT%H:%M%z"),
        "endDT":         end.strftime("%Y-%m-%dT%H:%M%z"),
        "format":        "json",
        "siteStatus":    "active",
    }

    try:
        r = client.get(NWIS_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        ts_list = data["value"]["timeSeries"]
        if not ts_list:
            LOG.warning("No time series for %s", gauge["name"])
            return _empty(gauge)

        values = ts_list[0]["values"][0]["value"]
        if not values:
            LOG.warning("No values for %s", gauge["name"])
            return _empty(gauge)

        # Parse into time series — filter out sentinel -999999
        series = []
        for v in values:
            try:
                cfs = float(v["value"])
                if cfs >= 0:
                    series.append({
                        "dt":  v["dateTime"][:16],   # trim seconds
                        "cfs": round(cfs, 0),
                    })
            except (ValueError, TypeError):
                pass

        if not series:
            return _empty(gauge)

        latest_cfs  = series[-1]["cfs"]
        latest_dt   = series[-1]["dt"]
        peak_cfs    = max(v["cfs"] for v in series)
        flood_stage = FLOOD_CFS.get(gauge["id"], 99999)
        flood_alert = latest_cfs >= flood_stage * 0.8   # warn at 80% of flood

        # 24hr change
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")
        day_ago = next((v["cfs"] for v in series if v["dt"] >= cutoff), None)
        change_24h = round(latest_cfs - day_ago, 0) if day_ago is not None else None

        LOG.info("  ✓ %-20s %7.0f cfs  24hr=%s  peak=%d",
                 gauge["name"], latest_cfs,
                 f"{change_24h:+.0f}" if change_24h is not None else "—",
                 peak_cfs)

        return {
            "id":          gauge["id"],
            "name":        gauge["name"],
            "location":    gauge["location"],
            "latest_cfs":  latest_cfs,
            "latest_dt":   latest_dt,
            "change_24h":  change_24h,
            "peak_7d_cfs": peak_cfs,
            "flood_cfs":   flood_stage,
            "flood_alert": flood_alert,
            "series":      series[-168:],   # last 7 days of hourly ≈ 168 pts
        }

    except Exception as e:
        LOG.warning("Failed %s: %s", gauge["name"], e)
        return _empty(gauge)


def _empty(gauge: dict) -> dict:
    return {
        "id":          gauge["id"],
        "name":        gauge["name"],
        "location":    gauge["location"],
        "latest_cfs":  None,
        "latest_dt":   None,
        "change_24h":  None,
        "peak_7d_cfs": None,
        "flood_cfs":   FLOOD_CFS.get(gauge["id"]),
        "flood_alert": False,
        "series":      [],
    }


def main():
    LOG.info("=== Stream Gauge Fetch ===")
    PROC_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    with httpx.Client() as client:
        for gauge in GAUGES:
            LOG.info("Fetching %s (%s)...", gauge["name"], gauge["id"])
            results.append(fetch_gauge(client, gauge))

    valid = [g for g in results if g["latest_cfs"] is not None]

    # Zero-write guard
    if not valid:
        LOG.error("All gauges failed — preserving existing gauges_latest.json")
        raise SystemExit(1)

    output = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "gauges":  results,
    }

    out_path = PROC_DIR / "gauges_latest.json"
    out_path.write_text(json.dumps(output, indent=2))
    LOG.info("Saved %s (%d gauges)", out_path, len(valid))

    # Copy to dashboard
    DASH_DIR.mkdir(exist_ok=True)
    (DASH_DIR / "gauges_latest.json").write_text(json.dumps(output, indent=2))
    LOG.info("Copied to dashboard/gauges_latest.json")

    # Summary
    print(f"\n{'='*55}")
    print(f"  Stream Gauge Snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")
    for g in results:
        cfs   = f"{g['latest_cfs']:,.0f}" if g["latest_cfs"] else "—"
        chg   = f"{g['change_24h']:+,.0f}" if g["change_24h"] is not None else "—"
        alert = " 🔴 FLOOD WATCH" if g["flood_alert"] else ""
        print(f"  {g['name']:20} {cfs:>8} cfs  24hr: {chg:>8}{alert}")
    print(f"{'='*55}")
    LOG.info("=== Done! ===")


if __name__ == "__main__":
    main()

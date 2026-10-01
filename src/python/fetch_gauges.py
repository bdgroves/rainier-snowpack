"""
fetch_gauges.py
Streamflow (cfs) for four rivers that drain Mt. Rainier, last 7 days.

Source: the USGS Water Data API (api.waterdata.usgs.gov), which replaces the
legacy WaterServices that USGS is switching off in early 2027. WaterServices is
kept as a fallback for now. Set USGS_API_KEY (repo secret) for a higher rate
limit; without it the anonymous limit is plenty for four gauges an hour.

The series is thinned to one point per hour so the chart really covers 7 days
(the old code kept the last 168 fifteen-minute readings — 42 hours).

FLOOD_CFS are rough flows for "minor flooding" at each gauge, for colouring only;
the NWS river forecasts are the authority.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
LOG = logging.getLogger(__name__)

GAUGES = [
    {"id": "12082500", "name": "Nisqually River", "location": "nr National"},
    {"id": "12083000", "name": "Mineral Creek",   "location": "nr Mineral"},
    {"id": "12101500", "name": "Puyallup River",  "location": "at Puyallup"},
    {"id": "12099200", "name": "White River",     "location": "ab Boise Cr, Buckley"},
]
FLOOD_CFS = {"12082500": 8000, "12083000": 3000, "12101500": 25000, "12099200": 8000}

API = "https://api.waterdata.usgs.gov/ogcapi/v0"
LEGACY = "https://waterservices.usgs.gov/nwis/iv/"
API_KEY = os.environ.get("USGS_API_KEY", "").strip()
PT = ZoneInfo("America/Los_Angeles")
PROC_DIR = Path("data/processed")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "rainier-snowpack (github.com/bdgroves/rainier-snowpack)"


def get(url, params=None):
    headers = {"X-Api-Key": API_KEY} if API_KEY and url.startswith(API) else {}
    for attempt in range(3):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def from_api(site):
    url, params, pts = f"{API}/collections/continuous/items", {
        "f": "json", "limit": 10000, "monitoring_location_id": f"USGS-{site}",
        "parameter_code": "00060", "time": "P7D"}, []
    for _ in range(10):
        d = get(url, params)
        for f in d.get("features", []):
            p = f["properties"]
            try:
                pts.append((datetime.fromisoformat(p["time"].replace("Z", "+00:00")), float(p["value"])))
            except (TypeError, ValueError, KeyError):
                pass
        nxt = next((l["href"] for l in d.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url, params = nxt, None
    return pts


def from_legacy(site):
    d = get(LEGACY, {"sites": site, "parameterCd": "00060", "period": "P7D", "format": "json", "siteStatus": "all"})
    pts = []
    for ts in d.get("value", {}).get("timeSeries", []):
        for v in ts["values"][0]["value"]:
            try:
                val = float(v["value"])
                if val >= 0:
                    pts.append((datetime.fromisoformat(v["dateTime"]), val))
            except (TypeError, ValueError):
                pass
    return pts


def fetch_gauge(g):
    pts, source = [], None
    for name, fn in (("water-data-api", from_api), ("waterservices", from_legacy)):
        try:
            pts = [(t, v) for t, v in fn(g["id"]) if v >= 0]
            if pts:
                source = name
                break
        except Exception as e:
            LOG.warning("  %s %s: %s", g["name"], name, e)
    base = {"id": g["id"], "name": g["name"], "location": g["location"],
            "flood_cfs": FLOOD_CFS.get(g["id"]), "source": source}
    if not pts:
        return dict(base, latest_cfs=None, latest_dt=None, change_24h=None,
                    peak_7d_cfs=None, flood_alert=False, series=[])
    pts.sort()
    t_last, latest = pts[-1]
    day_ago = next((v for t, v in pts if t >= t_last - timedelta(hours=24)), None)
    hourly = {}
    for t, v in pts:                           # last reading in each hour
        hourly[t.astimezone(PT).strftime("%Y-%m-%dT%H:00")] = v
    flood = FLOOD_CFS.get(g["id"])
    LOG.info("  ✓ %-16s %7.0f cfs  (%s, %d readings)", g["name"], latest, source, len(pts))
    return dict(base,
                latest_cfs=round(latest),
                latest_dt=t_last.astimezone(PT).strftime("%Y-%m-%dT%H:%M"),
                change_24h=round(latest - day_ago) if day_ago is not None else None,
                peak_7d_cfs=round(max(v for _, v in pts)),
                flood_alert=bool(flood and latest >= 0.8 * flood),
                series=[{"dt": k, "cfs": round(v)} for k, v in sorted(hourly.items())])


def main():
    LOG.info("=== Stream gauges ===")
    results = [fetch_gauge(g) for g in GAUGES]
    if not any(r["latest_cfs"] is not None for r in results):
        LOG.error("All gauges failed — keeping the published file")
        raise SystemExit(1)
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    (PROC_DIR / "gauges_latest.json").write_text(json.dumps({
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "times": "Pacific local time",
        "api_key": bool(API_KEY),
        "gauges": results,
    }, separators=(",", ":")))
    LOG.info("Saved gauges_latest.json")


if __name__ == "__main__":
    main()

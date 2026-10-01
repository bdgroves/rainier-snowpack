"""
fetch_hourly.py
48 hours of hourly air temperature (TOBS) from every station, on one shared
hourly axis, plus an estimate of the freezing level.

Freezing level: a straight-line fit of the latest temperature against station
elevation (the stations span ~2,250–5,800 ft), solved for 32 °F. It is a rough
guide to where rain turns to snow, not a sounding: inversions break it, and
outside the stations' elevation range it is an extrapolation, which the JSON
says.

Writes data/processed/hourly_temps.json (and .csv).
"""

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from stations import AWDB, STATIONS

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
LOG = logging.getLogger(__name__)
OUT_DIR = Path("data/processed")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "rainier-snowpack (github.com/bdgroves/rainier-snowpack)"


def fetch(triplet, start, end):
    params = {"stationTriplets": triplet, "elements": "TOBS", "duration": "HOURLY",
              "beginDate": str(start), "endDate": str(end)}
    for attempt in range(3):
        try:
            r = SESSION.get(f"{AWDB}/data", params=params, timeout=60)
            r.raise_for_status()
            d = r.json()
            vals = d[0]["data"][0].get("values", []) if d and d[0].get("data") else []
            return {v["date"][:16].replace("T", " "): float(v["value"]) for v in vals
                    if isinstance(v.get("value"), (int, float)) and -60 < v["value"] < 110}
        except Exception as e:
            if attempt == 2:
                LOG.warning("  %s failed: %s", triplet, e)
                return {}
            time.sleep(3)


def freezing_level(points):
    """points: [(elev_ft, temp_f)]. Returns dict or None."""
    if len(points) < 4:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx      # °F per ft
    lapse = slope * 1000                                                 # °F per 1000 ft
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(r * r for r in resid) / ss_tot if ss_tot else 0
    res = {"lapse_f_per_1000ft": round(lapse, 1), "r2": round(r2, 2), "n": len(points),
           "station_range_ft": [min(xs), max(xs)]}
    if lapse >= -0.5:
        # flat or inverted profile: the fit says nothing useful about a freezing level
        res.update(level_ft=None, note="inversion" if lapse > 0.5 else "flat")
        return res
    level = mx + (32 - my) / slope
    res["level_ft"] = int(round(level, -2))
    res["extrapolated"] = not (min(xs) <= level <= max(xs))
    if level < 0:
        res["level_ft"], res["note"] = 0, "below sea level (very cold air mass)"
    return res


def main():
    today = date.today()
    start, end = today - timedelta(days=2), today + timedelta(days=1)
    LOG.info("=== Hourly temps %s → %s ===", start, today)

    raw = {}
    for s in STATIONS:
        raw[s["name"]] = fetch(s["id"], start, end)
        LOG.info("  %-18s %d readings", s["name"], len(raw[s["name"]]))
    if not any(raw.values()):
        LOG.error("No hourly data — keeping the published file")
        raise SystemExit(1)

    # one axis: the last 48 hours that any station reported
    stamps = sorted(set().union(*[set(v) for v in raw.values()]))[-48:]
    labels = [datetime.strptime(t, "%Y-%m-%d %H:%M").strftime("%m/%d %H:%M") for t in stamps]
    out, rows = {}, []
    for s in STATIONS:
        temps = [raw[s["name"]].get(t) for t in stamps]
        clean = [t for t in temps if t is not None]
        out[s["name"]] = {
            "elevation": s["elevation_ft"], "group": s["group"], "labels": labels,
            "temps": [None if t is None else round(t, 1) for t in temps],
            "freezing": [t is not None and t <= 32 for t in temps],
            "min_temp": round(min(clean), 1) if clean else None,
            "max_temp": round(max(clean), 1) if clean else None,
            "latest": round(clean[-1], 1) if clean else None,
            "latest_time": next((stamps[i] for i in range(len(stamps) - 1, -1, -1) if temps[i] is not None), None),
        }
        rows += [{"datetime": t, "station_name": s["name"], "elevation_ft": s["elevation_ft"], "temp_f": v}
                 for t, v in zip(stamps, temps)]

    # freezing level now, and through the 48 h (latest reading per hour across stations)
    def fl_at(i):
        pts = [(s["elevation_ft"], out[s["name"]]["temps"][i]) for s in STATIONS if out[s["name"]]["temps"][i] is not None]
        return freezing_level(pts)
    now_i = max(i for i in range(len(stamps)) if sum(out[s["name"]]["temps"][i] is not None for s in STATIONS) >= 4) \
        if any(sum(out[s["name"]]["temps"][i] is not None for s in STATIONS) >= 4 for i in range(len(stamps))) else None
    meta = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "times": stamps,
        "freezing_level": dict(fl_at(now_i) or {}, time=stamps[now_i]) if now_i is not None else None,
        "freezing_level_series": [(fl_at(i) or {}).get("level_ft") for i in range(len(stamps))],
        "note": "Times are station local standard time (PST, UTC-8), as NRCS reports them.",
    }
    out["_meta"] = meta

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_DIR / "hourly_temps.csv", index=False)
    (OUT_DIR / "hourly_temps.json").write_text(json.dumps(out, separators=(",", ":")))
    fl = meta["freezing_level"] or {}
    LOG.info("Freezing level ≈ %s ft (lapse %s °F/1000 ft, r²=%s%s)", fl.get("level_ft"),
             fl.get("lapse_f_per_1000ft"), fl.get("r2"), ", extrapolated" if fl.get("extrapolated") else "")


if __name__ == "__main__":
    main()

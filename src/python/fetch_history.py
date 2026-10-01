"""
fetch_history.py
The long record: every complete water year at every station, plus the ENSO
index for each winter, so the dashboard can say how this year compares and
what El Niño and La Niña winters have actually meant on Rainier.

Writes data/processed/snow_history.json. Cheap to call hourly: it only goes
back to NRCS when the file is a week old or a water year has finished.
NOAA's ENSO outlook is refreshed every run (one small page).

Per station and water year:
  apr1     SWE on April 1 (the date water-supply forecasts key on)
  peak     maximum SWE and its date
  onset    first day of the continuous snow cover that contains the peak
  meltout  first day after the peak with SWE back to ~0
Per station:
  bands    day-of-water-year percentiles [min, p10, p25, p50, p75, p90, max]
           over all complete years (±3-day window), when there are ≥10 years
"""

import html
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from stations import AWDB, STATIONS, water_year, wy_end, wy_start

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
LOG = logging.getLogger(__name__)

OUT = Path("data/processed/snow_history.json")
FIRST_WY = 1979
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
OUTLOOK_URL = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "rainier-snowpack (github.com/bdgroves/rainier-snowpack)"


def get(url, params=None, raw=False, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=180)
            r.raise_for_status()
            return r.text if raw else r.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def daily_swe(triplet, through_wy):
    d = get(f"{AWDB}/data", {"stationTriplets": triplet, "elements": "WTEQ", "duration": "DAILY",
                            "beginDate": str(wy_start(FIRST_WY)), "endDate": str(wy_end(through_wy))})
    vals = {}
    for block in (d[0].get("data", []) if d else []):
        for v in block.get("values", []):
            if isinstance(v.get("value"), (int, float)) and v["value"] > -99:
                vals[v["date"][:10]] = float(v["value"])
    return vals


def doy(d: date) -> int:
    return (d - wy_start(water_year(d))).days + 1


def pct(sorted_vals, q):
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def season_stats(wy, vals):
    start = wy_start(wy)
    n = (wy_end(wy) - start).days + 1
    series = [vals.get(str(start + timedelta(days=i))) for i in range(n)]
    have = sum(v is not None for v in series)
    if have < 300:
        return None, None
    # April 1: exact, else the nearest reading within 3 days
    a1 = (date(wy, 4, 1) - start).days
    apr1 = next((series[a1 + k] for k in (0, -1, 1, -2, 2, -3, 3) if series[a1 + k] is not None), None)
    pk_i = max((i for i in range(n) if series[i] is not None), key=lambda i: series[i])
    peak = series[pk_i]
    onset = meltout = None
    if peak and peak >= 1:
        i = pk_i
        while i > 0 and (series[i - 1] is None or series[i - 1] > 0.1):
            i -= 1
        onset = i
        j = pk_i
        while j < n and (series[j] is None or series[j] > 0.1):
            j += 1
        meltout = j if j < n else None
    iso = lambda i: str(start + timedelta(days=i)) if i is not None else None
    row = {"apr1": apr1, "peak": peak, "peak_date": iso(pk_i), "peak_doy": pk_i + 1,
           "onset": iso(onset), "onset_doy": None if onset is None else onset + 1,
           "meltout": iso(meltout), "meltout_doy": None if meltout is None else meltout + 1,
           "days": have}
    return row, series


def bands(year_series):
    by = defaultdict(list)
    for s in year_series:
        for i, v in enumerate(s):
            if v is not None:
                by[i].append(v)
    out = []
    for i in range(366):
        vals = sorted(v for k in range(i - 3, i + 4) for v in by.get(k, ()))
        if len(vals) < 10 * 7:
            out.append(None)
            continue
        out.append([round(pct(vals, q), 1) for q in (0, .1, .25, .5, .75, .9, 1)])
    return out


def fetch_oni():
    txt = get(ONI_URL, raw=True)
    djf, latest = {}, None
    for line in txt.splitlines()[1:]:
        p = line.split()
        if len(p) != 4:
            continue
        latest = {"season": p[0], "year": int(p[1]), "oni": float(p[3])}
        if p[0] == "DJF":
            djf[int(p[1])] = float(p[3])        # DJF 1998 = the winter of water year 1998
    return djf, latest


def fetch_outlook():
    page = get(OUTLOOK_URL, raw=True)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    status = re.search(r"ENSO Alert System Status:\s*(.+?)(?=\s+Synopsis:|\s{2,}|$)", text)
    synopsis = re.search(r"Synopsis:\s*(.+?\.)(?=\s+[A-Z][a-z]|\s*$)", text)
    issued = re.search(rf"\b(\d{{1,2}} (?:{MONTHS}) \d{{4}})\b", text)
    if not (status and synopsis):
        raise ValueError("status/synopsis not found on the CPC page")
    return {"status": status.group(1).strip()[:120], "synopsis": synopsis.group(1).strip()[:400],
            "issued": issued.group(1) if issued else None, "url": OUTLOOK_URL}


def phase(oni):
    if oni is None:
        return None
    if oni >= 1.5:
        return "strong El Niño"
    if oni >= 0.5:
        return "El Niño"
    if oni <= -1.5:
        return "strong La Niña"
    if oni <= -0.5:
        return "La Niña"
    return "neutral"


def main():
    today = date.today()
    through = water_year(today) - 1
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    fresh = old.get("through_wy") == through and old.get("stations") and \
        (today - date.fromisoformat(old.get("fetched", "2000-01-01"))).days < 7

    out = dict(old) if fresh else {"through_wy": through, "fetched": str(today), "stations": {}}
    if not fresh:
        LOG.info("=== Rebuilding snow history through WY%d ===", through)
        for stn in STATIONS:
            try:
                vals = daily_swe(stn["id"], through)
            except Exception as e:
                LOG.warning("%s: %s — keeping previous history", stn["name"], e)
                if stn["id"] in old.get("stations", {}):
                    out["stations"][stn["id"]] = old["stations"][stn["id"]]
                continue
            years, series = {}, []
            for wy in range(FIRST_WY, through + 1):
                row, s = season_stats(wy, vals)
                if row:
                    years[str(wy)] = row
                    series.append(s)
            apr = sorted(r["apr1"] for r in years.values() if r["apr1"] is not None)
            out["stations"][stn["id"]] = {
                "name": stn["name"], "group": stn["group"], "elevation": stn["elevation_ft"],
                "first_wy": min(map(int, years)) if years else None, "n_years": len(years),
                "apr1_median": round(pct(apr, .5), 1) if apr else None,
                "years": years, "bands": bands(series) if len(series) >= 10 else None,
            }
            LOG.info("  %-18s %d complete years since %s", stn["name"], len(years), min(years) if years else "—")
        if not out["stations"]:
            LOG.error("No history fetched — keeping the old file")
            raise SystemExit(1)

    # Rainier Apr-1 index per year: each long-record station's Apr 1 as % of its own median, averaged
    long = [sid for sid, s in out["stations"].items() if s["group"] == "rainier" and s["n_years"] >= 20 and s["apr1_median"]]
    idx = {}
    for wy in range(FIRST_WY, through + 1):
        v = [100 * out["stations"][sid]["years"][str(wy)]["apr1"] / out["stations"][sid]["apr1_median"]
             for sid in long if str(wy) in out["stations"][sid]["years"] and out["stations"][sid]["years"][str(wy)]["apr1"] is not None]
        if len(v) >= max(2, len(long) // 2):
            idx[str(wy)] = {"pct": round(sum(v) / len(v)), "n": len(v)}
    out["apr1_index"] = {"stations": [out["stations"][s]["name"] for s in long], "years": idx}

    try:
        djf, latest = fetch_oni()
        out["oni"] = {str(k): v for k, v in djf.items()}
        out["oni_latest"] = latest
        out["phase"] = {str(k): phase(v) for k, v in djf.items()}
    except Exception as e:
        LOG.warning("ONI: %s", e)
    try:
        out["outlook"] = fetch_outlook()
    except Exception as e:
        LOG.warning("CPC outlook: %s", e)
    out["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    # only rewrite when something other than the timestamp changed
    cmp = lambda d: {k: v for k, v in d.items() if k != "generated"}
    if old and cmp(old) == cmp(out):
        LOG.info("History unchanged")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    LOG.info("Saved %s (%.0f KB)", OUT, OUT.stat().st_size / 1024)


if __name__ == "__main__":
    main()

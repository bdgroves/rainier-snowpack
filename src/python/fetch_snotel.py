"""
fetch_snotel.py
Daily SNOTEL data for the stations in stations.py, from the NRCS AWDB REST API,
with NRCS's own medians so every number can be read against normal.

Writes (data/processed/):
  snotel_daily.csv     one row per station per day, current water year
  basin_daily.csv      daily Rainier index + the old all-station average
  snotel_season.json   chart data: this year, median and last year, per station
                       and for the Rainier average, on a full Oct 1 – Sep 30 axis
  snotel_latest.json   current conditions per station + basin summary
and, once per year, data/archive/snotel_wy{YYYY}.csv for the year just ended.

The water year is worked out from the date, so nothing needs editing on Oct 1.

Medians: AWDB returns a median beside each value (NRCS 1991–2020 normals) but
returns nothing for dates that haven't happened yet. The median for a calendar
day is the same every year, so the full-season median curve comes from asking
for LAST water year with centralTendencyType=MEDIAN — which also gives last
year's actual values for comparison.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from stations import AWDB, STATIONS, water_year, wy_end, wy_start

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
LOG = logging.getLogger(__name__)

PROC_DIR = Path("data/processed")
ARCHIVE_DIR = Path("data/archive")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "rainier-snowpack (github.com/bdgroves/rainier-snowpack)"

MELT_ALERT_THRESHOLD = 0.5    # in of SWE lost in a day
SNOW_DAY_DEPTH = 2.0          # in of new depth counts as a snow day
SNOW_DAY_SWE = 0.2            # ...or this much SWE gained without depth falling
MIN_SWE_FOR_DENSITY = 0.5     # below this, depth/SWE noise makes density meaningless
MIN_MEDIAN_FOR_PCT = 1.0      # % of median is not shown until the median itself is ≥ 1"


def get_data(triplet, elements, start, end, median=False, retries=3):
    """{element: {date: {"value": v, "median": m}}} plus timing tendencies per element."""
    params = {"stationTriplets": triplet, "elements": ",".join(elements),
              "beginDate": str(start), "endDate": str(end), "duration": "DAILY"}
    if median:
        params["centralTendencyType"] = "MEDIAN"
    for attempt in range(retries):
        try:
            r = SESSION.get(f"{AWDB}/data", params=params, timeout=90)
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as e:
            if attempt == retries - 1:
                LOG.warning("  %s %s failed: %s", triplet, ",".join(elements), e)
                return {}, {}
            time.sleep(3 * (attempt + 1))
    out, timing = {}, {}
    for block in (payload[0].get("data", []) if payload else []):
        el = block["stationElement"]["elementCode"]
        vals = {}
        for v in block.get("values", []):
            val, med = v.get("value"), v.get("median")
            vals[v["date"][:10]] = {
                "value": float(val) if isinstance(val, (int, float)) and val > -99 else None,
                "median": float(med) if isinstance(med, (int, float)) and med > -99 else None,
            }
        out[el] = vals
        if block.get("timingCentralTendencies"):
            timing[el] = block["timingCentralTendencies"]
    return out, timing


def md(d: str) -> str:
    return d[5:10]


def latest(series):
    for i in range(len(series) - 1, -1, -1):
        if series[i] is not None:
            return i, series[i]
    return None, None


def change(series, idx):
    """Change from the previous reported day to idx."""
    if idx is None or idx == 0:
        return None
    for j in range(idx - 1, -1, -1):
        if series[j] is not None:
            return round(series[idx] - series[j], 2)
    return None


def days_since_snow(swe, depth, today_idx):
    """Days since the last day the pack visibly grew. None = no snow day yet this WY."""
    last = None
    for i in range(1, today_idx + 1):
        dd = depth[i] - depth[i - 1] if depth[i] is not None and depth[i - 1] is not None else None
        ds = swe[i] - swe[i - 1] if swe[i] is not None and swe[i - 1] is not None else None
        if (dd is not None and dd >= SNOW_DAY_DEPTH) or (ds is not None and ds >= SNOW_DAY_SWE and (dd is None or dd >= 0)):
            last = i
    return None if last is None else today_idx - last


def timing_date(t, wy):
    """NRCS {'month': 5, 'day': 4} → ISO date inside water year wy."""
    if not t or not t.get("month"):
        return None
    y = wy - 1 if t["month"] >= 10 else wy
    try:
        return date(y, t["month"], t["day"]).isoformat()
    except ValueError:
        return None


def r1(v, n=1):
    return None if v is None else round(v, n)


def main():
    today = date.today()
    wy = water_year(today)
    start, end = wy_start(wy), wy_end(wy)
    dates = [str(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    pos = {d: i for i, d in enumerate(dates)}
    LOG.info("=== SNOTEL fetch — water year %d (%s → %s) ===", wy, start, today)

    PROC_DIR.mkdir(parents=True, exist_ok=True)
    rows, latest_list, season = [], [], {}

    for stn in STATIONS:
        sid = stn["id"]
        LOG.info("%s (%s)", stn["name"], sid)
        cur, _ = get_data(sid, ["WTEQ", "SNWD", "TOBS", "PRCP", "PREC"], start, today, median=True)
        prev, timing = get_data(sid, ["WTEQ", "PREC"], wy_start(wy - 1), wy_end(wy - 1), median=True)

        def col(el, key="value", src=cur):
            s = [None] * len(dates)
            for d, v in src.get(el, {}).items():
                if d in pos:
                    s[pos[d]] = v[key]
            return s

        def by_md(el, key):
            m = {md(d): v[key] for d, v in prev.get(el, {}).items()}
            if "02-29" not in m and "02-28" in m:
                m["02-29"] = m["02-28"] if key == "median" else None
            return [m.get(md(d)) for d in dates]

        swe, depth, temp, prcp, prec = col("WTEQ"), col("SNWD"), col("TOBS"), col("PRCP"), col("PREC")
        swe_med, prec_med = by_md("WTEQ", "median"), by_md("PREC", "median")
        swe_last = by_md("WTEQ", "value")
        # prefer the current-year median where AWDB gave one (identical by design; this is belt and braces)
        for d, v in cur.get("WTEQ", {}).items():
            if d in pos and v["median"] is not None:
                swe_med[pos[d]] = v["median"]

        today_i = pos.get(str(today), len(dates) - 1)
        for i in range(today_i + 1):
            rows.append({"date": dates[i], "station_triplet": sid, "station_name": stn["name"],
                         "elevation_ft": stn["elevation_ft"], "group": stn["group"],
                         "swe_in": swe[i], "depth_in": depth[i], "temp_f": temp[i],
                         "precip_in": prcp[i], "precip_wytd_in": prec[i],
                         "median_swe_in": swe_med[i], "median_precip_wytd_in": prec_med[i]})

        li, lswe = latest(swe)
        _, ldepth = latest(depth[: (li if li is not None else today_i) + 1]) if li is not None else latest(depth)
        _, ltemp = latest(temp)
        pi, lprec = latest(prec)
        med_now = swe_med[li] if li is not None else None
        pct = round(100 * lswe / med_now) if lswe is not None and med_now and med_now >= MIN_MEDIAN_FOR_PCT else None
        prec_med_now = prec_med[pi] if pi is not None else None
        prec_pct = round(100 * lprec / prec_med_now) if lprec is not None and prec_med_now and prec_med_now >= 0.5 else None
        density = None
        if lswe is not None and ldepth and lswe >= MIN_SWE_FOR_DENSITY and ldepth >= 2:
            density = round(100 * lswe / ldepth, 1)
            if not 5 <= density <= 65:          # outside what snow can physically be: sensor noise
                density = None
        d24 = change(swe, li)
        t = timing.get("WTEQ", {})
        peak_med = t.get("medianPeak") or {}

        latest_list.append({
            "id": sid, "name": stn["name"], "elevation": stn["elevation_ft"],
            "lat": stn["lat"], "lon": stn["lon"], "group": stn["group"],
            "date": dates[li] if li is not None else None,
            "swe_in": lswe, "depth_in": ldepth, "temp_f": ltemp,
            "median_swe_in": med_now, "pct_median": pct,
            "last_year_swe_in": swe_last[li] if li is not None else None,
            "precip_wytd_in": lprec, "precip_median_in": prec_med_now, "precip_pct_median": prec_pct,
            "swe_change_24h": d24,
            "depth_change_24h": change(depth, latest(depth)[0]),
            "days_since_snow": days_since_snow(swe, depth, today_i),
            "melt_alert": bool(d24 is not None and d24 < -MELT_ALERT_THRESHOLD),
            "density_pct": density,
            "median_peak_in": peak_med.get("value"),
            "median_peak_date": timing_date(peak_med, wy),
            "median_onset_date": timing_date(t.get("medianOnset"), wy),
            "median_meltout_date": timing_date(t.get("medianMeltout"), wy),
        })
        season[sid] = {"swe": [r1(v) for v in swe], "median": [r1(v) for v in swe_med],
                       "last": [r1(v) for v in swe_last], "depth": [r1(v, 0) for v in depth],
                       "prec": [r1(v, 2) for v in prec], "prec_median": [r1(v, 2) for v in prec_med]}
        LOG.info("  SWE %s\" (median %s\", %s%%)  precip WYTD %s\" (%s%% of median)  through %s",
                 lswe, med_now, pct, lprec, prec_pct, dates[li] if li is not None else "—")

    # ── Guard: never overwrite good data with nothing ─────────────────────────
    reporting = [s for s in latest_list if s["swe_in"] is not None or s["precip_wytd_in"] is not None]
    if not reporting:
        LOG.error("No station returned data — keeping the published files")
        raise SystemExit(1)

    # ── Rainier average: stations in the rainier group that have NRCS medians ─
    rain_ids = [s["id"] for s in STATIONS if s["group"] == "rainier" and any(v is not None for v in season[s["id"]]["median"])]
    def mean_of(key, i, ids):
        v = [season[s][key][i] for s in ids if season[s][key][i] is not None]
        return (sum(v) / len(v), len(v)) if v else (None, 0)

    rainier = {"swe": [], "median": [], "last": [], "prec": [], "prec_median": [], "n": []}
    basin_rows = []
    for i, d in enumerate(dates):
        # matched pairs only, so a missing station can't shift the ratio
        pairs = [(season[s]["swe"][i], season[s]["median"][i]) for s in rain_ids
                 if season[s]["swe"][i] is not None and season[s]["median"][i] is not None]
        ppairs = [(season[s]["prec"][i], season[s]["prec_median"][i]) for s in rain_ids
                  if season[s]["prec"][i] is not None and season[s]["prec_median"][i] is not None]
        rainier["swe"].append(r1(sum(p[0] for p in pairs) / len(pairs)) if pairs else None)
        rainier["n"].append(len(pairs))
        rainier["median"].append(r1(mean_of("median", i, rain_ids)[0]))
        rainier["last"].append(r1(mean_of("last", i, rain_ids)[0]))
        rainier["prec"].append(r1(sum(p[0] for p in ppairs) / len(ppairs), 2) if ppairs else None)
        rainier["prec_median"].append(r1(mean_of("prec_median", i, rain_ids)[0], 2))
        if d <= str(today):
            all_swe = [season[s["id"]]["swe"][i] for s in STATIONS if season[s["id"]]["swe"][i] is not None]
            all_dep = [season[s["id"]]["depth"][i] for s in STATIONS if season[s["id"]]["depth"][i] is not None]
            msum = sum(p[1] for p in pairs)
            basin_rows.append({
                "date": d, "wy_day": i + 1,
                "rainier_swe": rainier["swe"][-1],
                "rainier_median": round(msum / len(pairs), 1) if pairs else None,
                "rainier_pct": round(100 * sum(p[0] for p in pairs) / msum) if pairs and msum >= MIN_MEDIAN_FOR_PCT * len(pairs) else None,
                "rainier_n": len(pairs),
                "basin_swe": round(sum(all_swe) / len(all_swe), 2) if all_swe else None,
                "basin_depth": round(sum(all_dep) / len(all_dep), 1) if all_dep else None,
                "n_stations": len(all_swe),
            })
    for j, row in enumerate(basin_rows):
        prev_swe = basin_rows[j - 1]["rainier_swe"] if j else None
        row["swe_delta"] = round(row["rainier_swe"] - prev_swe, 2) if row["rainier_swe"] is not None and prev_swe is not None else None

    # current Rainier index from each station's latest value
    rs = [s for s in latest_list if s["id"] in rain_ids and s["swe_in"] is not None and s["median_swe_in"] is not None]
    sum_swe, sum_med = sum(s["swe_in"] for s in rs), sum(s["median_swe_in"] for s in rs)
    rp = [s for s in latest_list if s["id"] in rain_ids and s["precip_wytd_in"] is not None and s["precip_median_in"]]
    sum_p, sum_pm = sum(s["precip_wytd_in"] for s in rp), sum(s["precip_median_in"] for s in rp)
    rl = [s for s in rs if s["last_year_swe_in"] is not None]
    index = {
        "stations": [s["name"] for s in rs],
        "n": len(rs),
        "swe_in": round(sum_swe / len(rs), 1) if rs else None,
        "median_in": round(sum_med / len(rs), 1) if rs else None,
        "pct_median": round(100 * sum_swe / sum_med) if rs and sum_med >= MIN_MEDIAN_FOR_PCT * len(rs) else None,
        "last_year_in": round(sum(s["last_year_swe_in"] for s in rl) / len(rl), 1) if rl else None,
        "precip_wytd_in": round(sum_p / len(rp), 1) if rp else None,
        "precip_median_in": round(sum_pm / len(rp), 1) if rp else None,
        "precip_pct_median": round(100 * sum_p / sum_pm) if rp and sum_pm >= 0.5 * len(rp) else None,
    }

    rain_latest = [s for s in latest_list if s["group"] == "rainier" and s["swe_in"] is not None]
    def avg(key, nd=1, src=rain_latest):
        v = [s[key] for s in src if s[key] is not None]
        return round(sum(v) / len(v), nd) if v else None
    days = [s["days_since_snow"] for s in rain_latest if s["days_since_snow"] is not None]
    data_through = max((s["date"] for s in latest_list if s["date"]), default=None)

    # ── Write ─────────────────────────────────────────────────────────────────
    pd.DataFrame(rows).to_csv(PROC_DIR / "snotel_daily.csv", index=False)
    pd.DataFrame(basin_rows).to_csv(PROC_DIR / "basin_daily.csv", index=False)
    season_out = {"water_year": wy, "start": str(start), "dates_n": len(dates), "today_index": pos.get(str(today)),
                  "rainier_ids": rain_ids, "rainier": rainier, "stations": season}
    (PROC_DIR / "snotel_season.json").write_text(json.dumps(season_out, separators=(",", ":")))
    (PROC_DIR / "snotel_latest.json").write_text(json.dumps({
        "updated": str(today),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "water_year": wy,
        "wy_day": pos.get(str(today), 0) + 1,
        "data_through": data_through,
        "index": index,
        "basin": {
            "swe_change_24h": avg("swe_change_24h", 2),
            "depth_change_24h": avg("depth_change_24h", 1),
            "days_since_snow": min(days) if days else None,
            "melt_alert": any(s["melt_alert"] for s in rain_latest),
            "density_pct": avg("density_pct", 1),
        },
        "stations": latest_list,
    }, indent=1))
    LOG.info("Saved snotel_daily.csv (%d rows), basin_daily.csv, snotel_season.json, snotel_latest.json", len(rows))

    # one-time archive of the water year that just ended
    last_wy = wy - 1
    arch = ARCHIVE_DIR / f"snotel_wy{last_wy}.csv"
    if not arch.exists():
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        arows = []
        for stn in STATIONS:
            d, _ = get_data(stn["id"], ["WTEQ", "SNWD", "TOBS", "PRCP", "PREC"], wy_start(last_wy), wy_end(last_wy), median=True)
            ds = sorted(set().union(*[set(v) for v in d.values()])) if d else []
            for day in ds:
                g = lambda el, k="value": d.get(el, {}).get(day, {}).get(k)
                arows.append({"date": day, "station_triplet": stn["id"], "station_name": stn["name"],
                              "elevation_ft": stn["elevation_ft"], "group": stn["group"],
                              "swe_in": g("WTEQ"), "depth_in": g("SNWD"), "temp_f": g("TOBS"),
                              "precip_in": g("PRCP"), "precip_wytd_in": g("PREC"),
                              "median_swe_in": g("WTEQ", "median"), "median_precip_wytd_in": g("PREC", "median")})
        if arows:
            pd.DataFrame(arows).to_csv(arch, index=False)
            LOG.info("Archived water year %d → %s (%d rows)", last_wy, arch, len(arows))

    print(f"\n{'=' * 60}\n  Rainier snowpack — WY{wy} day {pos.get(str(today), 0) + 1}, data through {data_through}")
    print(f"  Index: {index['swe_in']}\" SWE vs {index['median_in']}\" median "
          f"({index['pct_median']}% of median, {index['n']} stations)")
    print(f"  Precip WYTD: {index['precip_wytd_in']}\" vs {index['precip_median_in']}\" ({index['precip_pct_median']}%)\n{'=' * 60}")


if __name__ == "__main__":
    main()

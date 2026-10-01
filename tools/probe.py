"""Probe in Actions: NRCS station metadata + medians, long SWE history, MODIS/VIIRS freshness, token health."""
import base64, json, os, time, traceback
import requests
out = {}
AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
IDS = ["679:WA:SNTL","642:WA:SNTL","672:WA:SNTL","1085:WA:SNTL","418:WA:SNTL","375:WA:SNTL","420:WA:SNTL"]
def step(k, f):
    try: out[k] = f()
    except Exception: out[k] = {"error": traceback.format_exc()[-1500:]}
step("stations", lambda: requests.get(f"{AWDB}/stations", params={"stationTriplets": ",".join(IDS)}, timeout=60).json())
step("nearby", lambda: [ (s["stationTriplet"], s["name"], s.get("elevation"), s.get("latitude"), s.get("longitude"), s.get("beginDate"))
    for s in requests.get(f"{AWDB}/stations", params={"stateCds": "WA", "networkCds": "SNTL"}, timeout=60).json()
    if 46.3 <= (s.get("latitude") or 0) <= 47.4 and -122.2 <= (s.get("longitude") or 0) <= -120.9])
step("median", lambda: requests.get(f"{AWDB}/data", params={"stationTriplets": "679:WA:SNTL", "elements": "WTEQ", "beginDate": "2026-03-01", "endDate": "2026-03-05", "duration": "DAILY", "centralTendencyType": "MEDIAN"}, timeout=60).json())
step("normals", lambda: requests.get(f"{AWDB}/data", params={"stationTriplets": "679:WA:SNTL", "elements": "WTEQ", "beginDate": "2026-10-01", "endDate": "2027-09-30", "duration": "DAILY", "centralTendencyType": "MEDIAN", "returnFlags": "false"}, timeout=60).json().__repr__()[:1500])
def hist():
    d = requests.get(f"{AWDB}/data", params={"stationTriplets": "679:WA:SNTL", "elements": "WTEQ", "beginDate": "1980-10-01", "endDate": "2026-09-30", "duration": "DAILY"}, timeout=120).json()
    v = d[0]["data"][0]["values"]
    return {"n": len(v), "first": v[:2], "last": v[-2:], "apr1": [x for x in v if x["date"].endswith("-04-01")][:50]}
step("paradise_hist", hist)
def cmr(short, ver):
    r = requests.get("https://cmr.earthdata.nasa.gov/search/granules.json", params={"short_name": short, "version": ver, "bounding_box": "-122.5,46,-121,47.5", "sort_key": "-start_date", "page_size": 3}, timeout=60).json()
    return [(e["title"], e.get("time_start"), e.get("collection_concept_id")) for e in r["feed"]["entry"]]
step("cmr_MOD10A1", lambda: cmr("MOD10A1", "61"))
step("cmr_MYD10A1", lambda: cmr("MYD10A1", "61"))
step("cmr_VNP10A1", lambda: cmr("VNP10A1", "2"))
step("cmr_VJ110A1", lambda: cmr("VJ110A1", "2"))
step("cmr_VNP10A1F", lambda: cmr("VNP10A1F", "2"))
step("cmr_old_concept", lambda: [(e["title"], e.get("time_start")) for e in requests.get("https://cmr.earthdata.nasa.gov/search/granules.json", params={"concept_id": "C2565093311-NSIDC_CPRD", "bounding_box": "-122.5,46,-121,47.5", "sort_key": "-start_date", "page_size": 3}, timeout=60).json()["feed"]["entry"]])
def token():
    t = os.environ.get("EARTHDATA_TOKEN", "")
    res = {"present": bool(t)}
    if t.count(".") == 2:
        try:
            p = json.loads(base64.urlsafe_b64decode(t.split(".")[1] + "=="))
            res["exp"] = time.strftime("%Y-%m-%d", time.gmtime(p.get("exp", 0)))
            res["iat"] = time.strftime("%Y-%m-%d", time.gmtime(p.get("iat", 0)))
        except Exception as e:
            res["decode"] = str(e)
    # try a real protected download (HEAD-ish, first bytes)
    try:
        e = requests.get("https://cmr.earthdata.nasa.gov/search/granules.json", params={"short_name": "MOD10A1", "version": "61", "bounding_box": "-122.5,46,-121,47.5", "sort_key": "-start_date", "page_size": 1}, timeout=60).json()["feed"]["entry"][0]
        url = next(l["href"] for l in e["links"] if l["href"].endswith(".hdf") and "http" in l["href"])
        r = requests.get(url, headers={"Authorization": f"Bearer {t}"}, stream=True, timeout=60, allow_redirects=True)
        res["download_status"] = r.status_code
        res["url_host"] = url.split("/")[2]
    except Exception as ex:
        res["download_error"] = str(ex)[:300]
    return res
step("token", token)
json.dump(out, open("tools/probe_out.json", "w"), indent=1, default=str)

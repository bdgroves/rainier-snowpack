"""
stations.py
One list of SNOTEL stations for every script, with NRCS's own coordinates and
elevations (checked against the AWDB /stations endpoint, Oct 2026).

group:
  rainier  — on the mountain or on the Cascade crest right beside it. The
             "Rainier" index on the dashboard is built from these.
  regional — the two original stations that sit well north of the park
             (Snoqualmie Pass area). Kept for continuity with the old
             7-station basin average, shown, but left out of the index.
"""

from datetime import date

STATIONS = [
    {"id": "679:WA:SNTL",  "name": "Paradise",          "elevation_ft": 5150, "lat": 46.78266, "lon": -121.74767, "group": "rainier"},
    {"id": "1085:WA:SNTL", "name": "Cayuse Pass",       "elevation_ft": 5260, "lat": 46.86956, "lon": -121.53434, "group": "rainier"},
    {"id": "642:WA:SNTL",  "name": "Morse Lake",        "elevation_ft": 5400, "lat": 46.90585, "lon": -121.48270, "group": "rainier"},
    {"id": "418:WA:SNTL",  "name": "Corral Pass",       "elevation_ft": 5810, "lat": 47.01872, "lon": -121.46464, "group": "rainier"},
    {"id": "375:WA:SNTL",  "name": "Bumping Ridge",     "elevation_ft": 4600, "lat": 46.81003, "lon": -121.33058, "group": "rainier"},
    {"id": "1257:WA:SNTL", "name": "Skate Creek",       "elevation_ft": 3770, "lat": 46.64336, "lon": -121.83044, "group": "rainier"},
    {"id": "941:WA:SNTL",  "name": "Mowich",            "elevation_ft": 3170, "lat": 46.92833, "lon": -121.95232, "group": "rainier"},
    {"id": "928:WA:SNTL",  "name": "Huckleberry Creek", "elevation_ft": 2250, "lat": 47.06565, "lon": -121.58778, "group": "rainier"},
    {"id": "672:WA:SNTL",  "name": "Olallie Meadows",   "elevation_ft": 4010, "lat": 47.37406, "lon": -121.44213, "group": "regional"},
    {"id": "420:WA:SNTL",  "name": "Cougar Mountain",   "elevation_ft": 3210, "lat": 47.27666, "lon": -121.67138, "group": "regional"},
]

AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"


def water_year(d: date) -> int:
    return d.year + 1 if d.month >= 10 else d.year


def wy_start(wy: int) -> date:
    return date(wy - 1, 10, 1)


def wy_end(wy: int) -> date:
    return date(wy, 9, 30)

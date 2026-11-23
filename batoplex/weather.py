"""Weather data for the Weather screen saver. No pygame here.

Requests (only these leave the box, and only while Weather shows, in its Preview, or when
the user saves a place):
- plex.tv/api/v2/geoip: the place of the box's IP, at most once a day (Location Automatic).
  No token; plex.tv sees the IP, as at each sign-in.
- geocoding-api.open-meteo.com: the typed ZIP code or city, once, when it is saved.
- api.open-meteo.com: the forecast for the place (2 decimals, about 1 km), each 30 minutes.
Open-Meteo is free for non-commercial use, needs no key, and its data is CC BY 4.0; the
crawl credits it. Its own opener: no Plex header, no token, no client id.
"""
import json
import os
import time
import urllib.parse
import urllib.request

from . import config, log

L = log.get("weather")

GEOIP = "https://plex.tv/api/v2/geoip"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 5
FRESH_S = 30 * 60
SHOW_S = 6 * 3600              # older data: the No data page
GEOIP_S = 24 * 3600
RETRY_S = (60, 120, 300, 900)  # after a failure; then each 15 minutes
FAHRENHEIT = {"US", "PR", "GU", "VI", "AS", "MP", "UM", "BS", "BZ", "KY", "LR", "PW", "FM", "MH"}
CURRENT = ("temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
           "wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl,dew_point_2m,is_day")
HOURLY = "precipitation,precipitation_probability,weather_code"
DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
         "sunrise,sunset")

# WMO weather code -> (sky text, icon). Icons: sunny, clear (night), partly, cloudy, fog,
# drizzle, rain, heavy, snow, sleet, storm, showers.
WMO = {
    0: ("Sunny", "sunny"), 1: ("Mostly Sunny", "partly"), 2: ("Partly Cloudy", "partly"),
    3: ("Cloudy", "cloudy"), 45: ("Fog", "fog"), 48: ("Freezing Fog", "fog"),
    51: ("Light Drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy Drizzle", "drizzle"),
    56: ("Freezing Drizzle", "sleet"), 57: ("Freezing Drizzle", "sleet"),
    61: ("Light Rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy Rain", "heavy"),
    66: ("Freezing Rain", "sleet"), 67: ("Freezing Rain", "sleet"),
    71: ("Light Snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy Snow", "snow"),
    77: ("Snow Grains", "snow"), 80: ("Showers", "showers"), 81: ("Showers", "showers"),
    82: ("Heavy Showers", "heavy"), 85: ("Snow Showers", "snow"), 86: ("Snow Showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("T'Storm, Hail", "storm"), 99: ("T'Storm, Hail", "storm"),
}
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def sky(code, day=True):
    """(text, icon) of a WMO code; at night Sunny is Clear."""
    text, ic = WMO.get(int(code or 0), ("Cloudy", "cloudy"))
    if not day and ic in ("sunny", "partly"):
        text = {"Sunny": "Clear", "Mostly Sunny": "Mostly Clear"}.get(text, text)
        ic = "clear" if ic == "sunny" else "partly_night"
    return text, ic


def units_for(country):
    return "f" if (country or "").upper() in FAHRENHEIT else "c"


def wind_text(deg, speed, units):
    if speed is None or speed < 1:
        return "Calm"
    d = COMPASS[int(((deg or 0) + 22.5) // 45) % 8]
    return "%s %d" % (d, round(speed))


# ------------------------------------------------------------ network
def _get(url, params=None, opener=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "BatoplexCRT/%s" % config.VERSION,
                                               "Accept": "application/json"})
    op = opener or urllib.request.build_opener()
    with op.open(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def locate(opener=None):
    """The place of the box's IP: {name, lat, lon, country}; None when not known."""
    d = _get(GEOIP, opener=opener)
    coords = (d.get("coordinates") or "").split(",")
    if len(coords) != 2:
        return None
    sub = d.get("subdivisions") or ""
    name = d.get("city") or sub or d.get("country") or ""
    return {"name": name, "lat": round(float(coords[0]), 2), "lon": round(float(coords[1]), 2),
            "country": d.get("code") or "", "region": sub}


def geocode(text, country=None, opener=None):
    """Places for a typed ZIP code or city: [{name, region, country, lat, lon}] (5 at most).
    A ZIP of digits only is looked up in the last known country."""
    q = {"name": text.strip(), "count": 5, "language": "en", "format": "json"}
    if text.strip().isdigit() and country:
        q["countryCode"] = country
    d = _get(GEOCODE, q, opener)
    out = []
    for r in d.get("results") or []:
        out.append({"name": r.get("name") or "", "region": r.get("admin1") or "",
                    "country": r.get("country_code") or "",
                    "lat": round(float(r["latitude"]), 2), "lon": round(float(r["longitude"]), 2)})
    return out


def fetch(place, units, opener=None):
    """The forecast reply for a place, in the units ("f" or "c")."""
    q = {"latitude": place["lat"], "longitude": place["lon"], "current": CURRENT,
         "hourly": HOURLY, "daily": DAILY, "timezone": "auto", "forecast_days": 4,
         "forecast_hours": 8}
    if units == "f":
        q.update(temperature_unit="fahrenheit", wind_speed_unit="mph",
                 precipitation_unit="inch")
    else:
        q.update(wind_speed_unit="kmh")
    return _get(FORECAST, q, opener)


# ------------------------------------------------------------ cache
def cache_path():
    return os.path.join(config.data_dir(), "weather.json")


def load_cache(path=None):
    try:
        with open(path or cache_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(d, path=None):
    try:
        config.atomic_write(path or cache_path(), json.dumps(d), fsync=False)
    except OSError as e:
        L.info("weather cache not saved: %s", e)


def retry_s(fails):
    return RETRY_S[min(fails, len(RETRY_S)) - 1] if fails else 0


# ------------------------------------------------------------ text
def _day_name(iso):
    t = time.strptime(iso, "%Y-%m-%d")
    return time.strftime("%A", t)


def _part(hi_lo, value, units):
    return "%s near %d" % (hi_lo, round(value))


def local_forecast(reply):
    """Three short forecast texts, 1990s local-forecast style: [(title, text)]."""
    d = reply.get("daily") or {}
    cur = reply.get("current") or {}
    days = d.get("time") or []
    if len(days) < 3:
        return []
    out = []
    day = bool(cur.get("is_day", 1))
    order = [("Today", 0, "max"), ("Tonight", 0, "min"), ("Tomorrow", 1, "max")] if day else \
        [("Tonight", 0, "min"), ("Tomorrow", 1, "max"), ("Tomorrow Night", 1, "min")]
    for title, k, which in order:
        text, _ic = sky(d["weather_code"][k], which == "max")
        t = d["temperature_2m_%s" % which][k]
        pop = (d.get("precipitation_probability_max") or [0] * 4)[k] or 0
        line = "%s. %s %d." % (text, "High" if which == "max" else "Low", round(t))
        if pop >= 30:
            line += " Chance of precipitation %d percent." % (round(pop / 10.0) * 10)
        out.append((title, line))
    return out


def extended(reply):
    """The next three days: [(day name, sky text, icon, high, low)]."""
    d = reply.get("daily") or {}
    days = d.get("time") or []
    out = []
    for k in range(1, min(4, len(days))):
        text, ic = sky(d["weather_code"][k])
        out.append((_day_name(days[k])[:3].upper(), text, ic,
                    round(d["temperature_2m_max"][k]), round(d["temperature_2m_min"][k])))
    return out


GRID = (7, 5)                  # columns, rows
GRID_STEP = 0.3                # degrees of latitude between rows (about 33 km)
GRID_HOURS = 6


def fetch_grid(place, units, opener=None):
    """The precipitation forecast for the next hours on a grid around the place, in one call:
    {"hours": ["2 PM", ...], "cells": [rows][cols][hours]} (inch or mm)."""
    import math
    cols, rows = GRID
    dlon = GRID_STEP / max(0.2, math.cos(math.radians(place["lat"])))
    lats, lons = [], []
    for r in range(rows):
        for c in range(cols):
            lats.append(round(place["lat"] + (rows // 2 - r) * GRID_STEP, 2))
            lons.append(round(place["lon"] + (c - cols // 2) * dlon * 0.75, 2))
    q = {"latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons)),
         "hourly": "precipitation", "forecast_hours": GRID_HOURS, "timezone": "auto"}
    if units == "f":
        q["precipitation_unit"] = "inch"
    d = _get(FORECAST, q, opener)
    if isinstance(d, dict):
        d = [d]
    cells = [[None] * cols for _ in range(rows)]
    for i, loc in enumerate(d[:rows * cols]):
        cells[i // cols][i % cols] = [v or 0 for v in (loc.get("hourly") or {}).get(
            "precipitation", [])]
    times = ((d[0].get("hourly") or {}).get("time") or [])[:GRID_HOURS]
    hours = [time.strftime("%I %p", time.strptime(t, "%Y-%m-%dT%H:%M")).lstrip("0")
             for t in times]
    return {"hours": hours, "cells": cells}


# Radar-style levels of one hour's amount (mm): 0 = none, then 1 (light) to 5 (heavy).
LEVELS_MM = (0.1, 0.5, 2.0, 5.0, 10.0)


def grid_frames(grid, units, nx, ny):
    """The grid interpolated to nx x ny cells for each hour: [bytes] of levels 0-5, row by
    row (worker thread; no pygame)."""
    cells = grid["cells"]
    rows, cols = len(cells), len(cells[0])
    k_mm = 25.4 if units == "f" else 1.0
    out = []
    for k in range(len(grid["hours"])):
        def v(r, c):
            a = cells[r][c] or []
            return (a[k] if k < len(a) else 0) * k_mm
        buf = bytearray(nx * ny)
        for j in range(ny):
            fy = j / (ny - 1) * (rows - 1)
            r0 = min(rows - 2, int(fy))
            wy = fy - r0
            for i in range(nx):
                fx = i / (nx - 1) * (cols - 1)
                c0 = min(cols - 2, int(fx))
                wx = fx - c0
                val = (v(r0, c0) * (1 - wx) + v(r0, c0 + 1) * wx) * (1 - wy) + \
                    (v(r0 + 1, c0) * (1 - wx) + v(r0 + 1, c0 + 1) * wx) * wy
                lv = 0
                for lim in LEVELS_MM:
                    if val >= lim:
                        lv += 1
                buf[j * nx + i] = lv
        out.append(bytes(buf))
    return out

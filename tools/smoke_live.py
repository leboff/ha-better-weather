"""Live smoke test: real Open-Meteo forecast through the integration's pure pipeline."""
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/ha-better-weather")
from custom_components.better_weather.api import parse_members
from custom_components.better_weather.blend import aggregate_daily, blend_members
from custom_components.better_weather.const import VARIABLES
from custom_components.better_weather.correction import compute_corrections
from custom_components.better_weather.nice import day_score

TZ = ZoneInfo("America/New_York")
LAT, LON = 39.93999, -75.18101
M = ["gem_seamless", "icon_seamless", "ecmwf_ifs025"]


def q(url, **p):
    p.update(latitude=LAT, longitude=LON, models=",".join(M), timezone="UTC", wind_speed_unit="kmh")
    return json.load(urllib.request.urlopen(url + "?" + urllib.parse.urlencode(p), timeout=60))


fc = q("https://api.open-meteo.com/v1/forecast", hourly=",".join(VARIABLES), forecast_days=8)
fx = json.load(open("/root/ha-better-weather/tests/fixtures/openmeteo_previous_runs.json"))
st = json.load(open("/root/ha-better-weather/tests/fixtures/station_statistics.json"))
F2C = lambda f: (f - 32) * 5 / 9
K = 1.609344
conv = {"temperature": F2C, "dew_point": F2C, "wind_speed": lambda x: x * K, "wind_gust": lambda x: x * K, "irradiance": lambda x: x}
stats = {}
for k, f in conv.items():
    stats[k] = [
        dict(r, **{s: (f(r[s]) if r[s] is not None else None) for s in ("mean", "min", "max")},
             flat=(r["max"] is not None and abs(r["max"] - r["min"]) < 0.01))
        for r in st[f"sensor.league_{k}"]
    ]
corr = compute_corrections(parse_members(fx, M, previous=True), stats, date(2026, 10, 15), 60, TZ, LAT, LON)
mem = parse_members(fc, M)
rows, raw = blend_members(mem, corr), blend_members(mem)
tom = date.today() + timedelta(days=1)
for name, r in [("corrected", rows), ("raw", raw)]:
    s, a = day_score(r, tom, TZ, LAT, LON)
    print(name, "tomorrow", tom, "score", s, "hi", a["high_f"], "lo", a["low_f"], "dew", a["dew_point_avg_f"],
          "rain_hrs", a["rain_hours"], "spread", a["model_spread_f"])
for d in aggregate_daily(rows, TZ)[:8]:
    print(d["datetime"][:10], round(d["native_temperature"] * 1.8 + 32), round(d["native_templow"] * 1.8 + 32),
          d["condition"], round(d.get("native_precipitation", 0) / 25.4, 2), d.get("precipitation_probability"))

if "--hours" in sys.argv:
    from custom_components.better_weather.blend import group
    for r in rows:
        loc = r["time"].astimezone(TZ)
        if loc.date() == tom:
            members = [mem[m].get(r["time"], {}).get("weather_code") for m in M]
            print(loc.strftime("%H"), "codes", members, "->", r.get("weather_code"), r["condition"],
                  "precip_mm", round(r.get("precipitation", 0), 2), "pop", r.get("precipitation_probability"))

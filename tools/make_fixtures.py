#!/usr/bin/env python3
"""Generate real-data fixtures + golden values for ha-better-weather tests."""
import json, math, os, sys, urllib.request, urllib.parse, statistics as st
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
sys.path.insert(0, "/root/nice-days")
from backfill import env
from solar import elevation

OUT = "/root/ha-better-weather/tests/fixtures"
os.makedirs(OUT, exist_ok=True)
TZ = ZoneInfo("America/New_York")
LAT, LON = 39.93999, -75.18101
MODELS = ["gem_seamless", "icon_seamless", "ecmwf_ifs025"]
AS_OF = date(2026, 10, 15)          # corrections computed as of this local date
WINDOW = 60

def get(url, **q):
    return json.load(urllib.request.urlopen(url + "?" + urllib.parse.urlencode(q), timeout=120))

# 1) live multi-model forecast (metric, as the integration will request it)
HOURLY = ["temperature_2m", "apparent_temperature", "dew_point_2m", "relative_humidity_2m", "precipitation",
          "precipitation_probability", "weather_code", "cloud_cover", "wind_speed_10m", "wind_gusts_10m",
          "wind_direction_10m", "shortwave_radiation", "pressure_msl", "uv_index", "is_day"]
fc = get("https://api.open-meteo.com/v1/forecast", latitude=LAT, longitude=LON, hourly=",".join(HOURLY),
         models=",".join(MODELS), forecast_days=8, timezone="UTC", wind_speed_unit="kmh")
json.dump(fc, open(f"{OUT}/openmeteo_forecast.json", "w"))

# 2) previous-runs (day-1) for the correction window, metric
start = AS_OF - timedelta(days=WINDOW + 1)
PREV = ["temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_gusts_10m", "shortwave_radiation"]
pr = get("https://previous-runs-api.open-meteo.com/v1/forecast", latitude=LAT, longitude=LON,
         hourly=",".join(f"{v}_previous_day1" for v in PREV), models=",".join(MODELS), timezone="UTC",
         wind_speed_unit="kmh", start_date=str(start), end_date=str(AS_OF - timedelta(days=1)))
json.dump(pr, open(f"{OUT}/openmeteo_previous_runs.json", "w"))

# 3) station hourly statistics in the shape recorder.statistics_during_period returns (sensor units: °F, mph, W/m², in/h)
raw = json.load(open("/root/nice-days/hourly.json"))
lo_ms = datetime.combine(start, datetime.min.time(), TZ).timestamp() * 1000
hi_ms = datetime.combine(AS_OF, datetime.min.time(), TZ).timestamp() * 1000
stats = {}
for k in ["temperature", "feels_like", "dew_point", "wind_speed", "wind_gust", "irradiance", "precipitation_intensity"]:
    stats[f"sensor.league_{k}"] = [dict(start=r["start"] / 1000, end=r["end"] / 1000, mean=r["mean"], min=r["min"], max=r["max"])
                                   for r in raw[f"sensor.league_{k}"] if lo_ms <= r["start"] < hi_ms]
json.dump(stats, open(f"{OUT}/station_statistics.json", "w"))

# 4) golden corrections: flat mean(model - station) over trailing WINDOW days, metric, frozen days excluded
F2C = lambda f: (f - 32) * 5 / 9
MPH2KMH = 1.609344
PRECEDING = {"shortwave_radiation", "precipitation"}
frozen, run = set(), []
for r in stats["sensor.league_temperature"]:
    if r["max"] is not None and r["min"] is not None and abs(r["max"] - r["min"]) < 0.01:
        run.append(r); continue
    if len(run) >= 3: frozen |= {datetime.fromtimestamp(x["start"], TZ).date() for x in run}
    run = []
if len(run) >= 3: frozen |= {datetime.fromtimestamp(x["start"], TZ).date() for x in run}
obs = {k: {datetime.fromtimestamp(r["start"], timezone.utc): r for r in v} for k, v in stats.items()}
times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in pr["hourly"]["time"]]
golden = {"as_of_local_date": str(AS_OF), "window_days": WINDOW, "frozen_days": sorted(map(str, frozen)), "models": {}}
for m in MODELS:
    g = {}
    for var, sid, conv, kind in [("temperature_2m", "temperature", F2C, "offset"), ("dew_point_2m", "dew_point", F2C, "offset"),
                                 ("wind_speed_10m", "wind_speed", lambda x: x * MPH2KMH, "ratio"),
                                 ("wind_gusts_10m", "wind_gust", lambda x: x * MPH2KMH, "ratio"),
                                 ("shortwave_radiation", "irradiance", lambda x: x, "ratio")]:
        diffs, fs, os_ = [], [], []
        for i, t in enumerate(times):
            f = pr["hourly"].get(f"{var}_previous_day1_{m}", [None] * len(times))[i]
            # Open-Meteo: shortwave_radiation/precipitation are means over the PRECEDING hour -> hour-start t-1h
            hs = t - timedelta(hours=1) if var in PRECEDING else t
            d = hs.astimezone(TZ).date()
            if d in frozen or not (AS_OF - timedelta(days=WINDOW) <= d < AS_OF): continue
            o = (obs[f"sensor.league_{sid}"].get(hs) or {}).get("max" if sid == "wind_gust" else "mean")
            if f is None or o is None: continue
            if var == "shortwave_radiation" and elevation(hs + timedelta(minutes=30)) < 20: continue
            o = conv(o)
            diffs.append(f - o); fs.append(f); os_.append(o)
        if kind == "offset":
            g[var] = dict(kind="offset", value=round(st.mean(diffs), 4), n=len(diffs))
        else:
            g[var] = dict(kind="ratio", value=round(sum(os_) / sum(fs), 4) if sum(fs) else None, n=len(fs))
    golden["models"][m] = g
json.dump(golden, open(f"{OUT}/golden_corrections.json", "w"), indent=1)

# 5) golden nice scores: render the canonical Jinja template for a grid of inputs
tpl = env().from_string(open("/root/nice-days/live_score.j2").read())
cases = []
import itertools, random
random.seed(1)
for _ in range(400):
    v = dict(feels_like=round(random.uniform(20, 100), 1), dew_point=round(random.uniform(10, 78), 1),
             wind_speed=round(random.uniform(0, 25), 1), wind_gust=round(random.uniform(0, 45), 1),
             irradiance=round(random.uniform(0, 1000), 1), precipitation_intensity=random.choice([0, 0, 0, 0, 0.02, 0.3]),
             sun_elevation=round(random.uniform(-20, 72), 1))
    vals = {f"sensor.league_{k}": v[k] for k in ["feels_like", "dew_point", "wind_speed", "wind_gust", "irradiance", "precipitation_intensity"]}
    v["expected_score"] = int(tpl.render(states=lambda e: vals[e], state_attr=lambda e, a: v["sun_elevation"]).strip())
    cases.append(v)
json.dump({"units": "imperial (°F, mph, W/m², in/h); score is 0-100 int, rounded half-to-even like Jinja round(0)", "cases": cases},
          open(f"{OUT}/golden_nice_scores.json", "w"), indent=0)
print("fixtures:", os.listdir(OUT)); print(json.dumps(golden["models"]["gem_seamless"], indent=1)); print("frozen in window:", golden["frozen_days"])

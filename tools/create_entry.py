"""Create the better_weather config entry for the League station (reads schema first)."""
import json, sys, requests, urllib3
urllib3.disable_warnings()
env = dict(l.strip().split("=", 1) for l in open("/root/.hermes/.env") if "=" in l and not l.startswith("#"))
B = env["HASS_URL"].strip('"').rstrip("/")
H = {"Authorization": f"Bearer {env['HASS_TOKEN'].strip(chr(34))}", "User-Agent": "x"}
P = lambda p, b: requests.post(B + p, json=b, headers=H, verify=False, timeout=60).json()

r = P("/api/config/config_entries/flow", {"handler": "better_weather", "show_advanced_options": True})
print("step:", r.get("step_id"), r.get("type"))
print("fields:", [(f["name"], f.get("required"), f.get("default", f.get("description", {}).get("suggested_value"))) for f in r.get("data_schema", [])])
if "--submit" not in sys.argv:
    requests.delete(B + f"/api/config/config_entries/flow/{r['flow_id']}", headers=H, verify=False)
    sys.exit()
want = {
    "name": "Better Weather",
    "station_temperature": "sensor.league_temperature",
    "station_dew_point": "sensor.league_dew_point",
    "station_humidity": "sensor.league_humidity",
    "station_wind_speed": "sensor.league_wind_speed",
    "station_wind_gust": "sensor.league_wind_gust",
    "station_wind_bearing": "sensor.league_wind_direction",
    "station_pressure": "sensor.league_relative_pressure",
    "station_irradiance": "sensor.league_irradiance",
    "station_precipitation_rate": "sensor.league_precipitation_intensity",
    "station_uv_index": "sensor.league_uv_index",
    "station_feels_like": "sensor.league_feels_like",
}
names = {f["name"] for f in r["data_schema"]}
body = {k: v for k, v in want.items() if k in names}
for f in r["data_schema"]:
    if f["name"] in ("latitude", "longitude"):
        body[f["name"]] = f.get("default", f.get("description", {}).get("suggested_value"))
print("unknown keys dropped:", set(want) - names)
res = P(f"/api/config/config_entries/flow/{r['flow_id']}", body)
print(json.dumps({k: res.get(k) for k in ("type", "title", "errors", "step_id")}))
if res.get("type") == "create_entry":
    print("entry_id:", res["result"]["entry_id"])

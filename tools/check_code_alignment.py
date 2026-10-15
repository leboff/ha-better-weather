"""Does Open-Meteo weather_code line up with precipitation at the same stamp, or with the preceding hour?"""
import json, urllib.request, urllib.parse
M = ["gem_seamless", "icon_seamless", "ecmwf_ifs025"]
p = dict(latitude=39.94, longitude=-75.181, models=",".join(M), timezone="UTC", hourly="weather_code,precipitation",
         past_days=30, forecast_days=1)
h = json.load(urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(p), timeout=60))["hourly"]
for m in M:
    wc, pr = h[f"weather_code_{m}"], h[f"precipitation_{m}"]
    n = len(wc)
    def agree(shift):
        ok = tot = 0
        for i in range(1, n - 1):
            j = i + shift
            if wc[i] is None or pr[j] is None: continue
            tot += 1; ok += (wc[i] >= 51) == (pr[j] > 0)
        return round(ok / tot, 3), tot
    print(m, "same stamp", agree(0), "| code vs next-stamp precip", agree(1), "| code vs prev-stamp precip", agree(-1))

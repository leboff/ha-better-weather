"""Pure Python port of tools/live_score.j2 and hourly/day scoring."""

import math
from datetime import timedelta
from statistics import mean

from .const import DEFAULTS, OPTION_DEFAULTS
from .solar import elevation


def score_components(
    feels_f, dew_f, wind_mph, gust_mph, irr_wm2, rain_rate, sun_elev_deg, cfg=None
):
    c = DEFAULTS | (cfg or {})

    def clamp(v):
        return max(0, min(1, v))

    f = clamp(sun_elev_deg / c["band_full_sun_elev"])
    lo = c["dusk_lo"] + (c["day_lo"] - c["dusk_lo"]) * f
    hi = c["dusk_hi"] + (c["day_hi"] - c["dusk_hi"]) * f
    temp = (
        max(0, 1 - (lo - feels_f) / c["cold_falloff"])
        if feels_f < lo
        else max(0, 1 - (feels_f - hi) / c["hot_falloff"])
        if feels_f > hi
        else 1
    )
    dew = clamp(1 - (dew_f - c["dew_ok"]) / (c["dew_bad"] - c["dew_ok"]))
    wind = clamp(
        min(
            1,
            1 - (wind_mph - c["wind_ok"]) / (c["wind_bad"] - c["wind_ok"]),
            1 - (gust_mph - c["gust_ok"]) / (c["gust_bad"] - c["gust_ok"]),
        )
    )
    num = c["w_temp"] * temp + c["w_dew"] * dew + c["w_wind"] * wind
    den = c["w_temp"] + c["w_dew"] + c["w_wind"]
    sun = None
    if sun_elev_deg > c["sun_min_elev"]:
        sz = math.sin(math.radians(sun_elev_deg))
        clear = 1098 * sz * math.exp(-0.057 / sz)
        sun = min(1, irr_wm2 / (clear * c["clear_sky_frac"]))
        num += c["w_sun"] * sun
        den += c["w_sun"]
    score = 100 * num / den
    if rain_rate > 0:
        score = min(score, c["rain_cap"])
    return int(round(score)), {
        "s_temp": round(temp, 2),
        "s_dew": round(dew, 2),
        "s_wind": round(wind, 2),
        "s_sun": round(sun, 2) if sun is not None else None,
    }


def nice_score(
    feels_f, dew_f, wind_mph, gust_mph, irr_wm2, rain_rate, sun_elev_deg, cfg=None
):
    return score_components(
        feels_f, dew_f, wind_mph, gust_mph, irr_wm2, rain_rate, sun_elev_deg, cfg
    )[0]


def forecast_score(row, latitude, longitude, cfg=None):
    required = (
        "apparent_temperature",
        "dew_point_2m",
        "wind_speed_10m",
        "wind_gusts_10m",
        "score_irradiance",
    )
    if any(row.get(k) is None for k in required):
        return None
    return score_components(
        row["apparent_temperature"] * 1.8 + 32,
        row["dew_point_2m"] * 1.8 + 32,
        row["wind_speed_10m"] / 1.609344,
        row["wind_gusts_10m"] / 1.609344,
        row["score_irradiance"],
        row.get("precipitation", 0),
        elevation(row["time"] + timedelta(minutes=30), latitude, longitude),
        cfg,
    )


def day_score(rows, day, tz, latitude, longitude, cfg=None, observed=None, now=None):
    """Mix elapsed observed hours and future forecasts; never invent missing observations."""
    c = OPTION_DEFAULTS | (cfg or {})
    hourly, rain_hours = [], 0
    for row in rows:
        t = row["time"]
        local = t.astimezone(tz)
        if (
            local.date() != day
            or not c["nice_wake_hour"] <= local.hour < c["nice_sleep_hour"]
        ):
            continue
        source = "forecast"
        obs = (observed or {}).get(t.isoformat())
        if now is not None and t + timedelta(hours=1) <= now:
            if obs is None:
                continue
            score, rain = obs["score"], obs["rain"]
            source = "station"
        else:
            value = forecast_score(row, latitude, longitude, c)
            if value is None:
                continue
            score = value[0]
            rain = row.get("precipitation", 0) >= 0.25
        if rain:
            score = min(score, 10)
            rain_hours += 1
        hourly.append(dict(hour=t.isoformat(), score=score, source=source))
    score = mean(h["score"] for h in hourly) if hourly else None
    if rain_hours and score is not None:
        score = min(score, c["day_rain_cap"])
    attrs = dict(
        hourly=hourly,
        rain_hours=rain_hours,
        observed_hours=sum(h["source"] == "station" for h in hourly),
        forecast_hours=sum(h["source"] == "forecast" for h in hourly),
    )
    eligible = [r for r in rows if r["time"].astimezone(tz).date() == day]
    for key, var, fn, factor, offset in [
        ("high_f", "temperature_2m", max, 1.8, 32),
        ("low_f", "temperature_2m", min, 1.8, 32),
        ("dew_point_avg_f", "dew_point_2m", mean, 1.8, 32),
        ("model_spread_f", "model_spread_temp", mean, 1.8, 0),
    ]:
        values = [r[var] for r in eligible if r.get(var) is not None]
        attrs[key] = round(fn(values) * factor + offset, 2) if values else None
    return round(score, 1) if score is not None else None, attrs

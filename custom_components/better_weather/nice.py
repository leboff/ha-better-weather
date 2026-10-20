"""Pure Python port of tools/live_score.j2 and hourly/day scoring."""

import math
from datetime import timedelta
from itertools import pairwise
from statistics import mean

from .const import DEFAULTS, FORECAST_RAIN_MM, OPTION_DEFAULTS
from .solar import elevation


def clear_sky_fraction(c, sun_elev_deg):
    """Station clear-sky fraction at this sun angle: learned curve if present, else the flat option.

    The curve is a list of [elevation_deg, fraction] points (ascending) learned from the station's own
    clear hours; between points it is linear, outside them it holds the end value.
    """
    curve = c.get("clear_sky_curve") or []
    if not curve:
        return c["clear_sky_frac"]
    if sun_elev_deg <= curve[0][0]:
        return curve[0][1]
    for (e0, f0), (e1, f1) in pairwise(curve):
        if sun_elev_deg <= e1:
            return f0 + (f1 - f0) * (sun_elev_deg - e0) / (e1 - e0)
    return curve[-1][1]


def _f(v):
    return f"{v:.0f}"


def breakdown(parts, rain_capped, rain_cap):
    """Plain-English 'why': each factor's points out of its max, biggest loss first."""
    rows = sorted(parts, key=lambda p: p["lost"], reverse=True)
    bits = [f"{p['label']} −{p['lost']:.0f}: {p['detail']}" for p in rows if p["lost"] >= 0.5]
    if rain_capped:
        bits.insert(0, f"Raining: capped at {rain_cap:.0f}")
    return rows, " · ".join(bits) if bits else "Nothing holding it back"


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
    sun = sun_raw = None
    if sun_elev_deg > c["sun_min_elev"]:
        sz = math.sin(math.radians(sun_elev_deg))
        clear = 1098 * sz * math.exp(-0.057 / sz)
        sun_raw = irr_wm2 / (clear * clear_sky_fraction(c, sun_elev_deg))
        sun = min(1, sun_raw)
        num += c["w_sun"] * sun
        den += c["w_sun"]
    score = 100 * num / den
    rain_capped = rain_rate > 0 and score > c["rain_cap"]
    if rain_rate > 0:
        score = min(score, c["rain_cap"])

    def part(key, label, weight, s, detail):
        mx = 100 * weight / den
        return {
            "factor": key,
            "label": label,
            "points": round(mx * s, 1),
            "max": round(mx, 1),
            "lost": round(mx * (1 - s), 1),
            "detail": detail,
        }

    temp_word = "in range" if temp == 1 else "cool" if feels_f < lo else "warm"
    parts = [
        part("temp", "Temp", c["w_temp"], temp,
             f"feels {_f(feels_f)}°, {temp_word} (ideal {_f(lo)}–{_f(hi)}° right now)"),
        part("dew", "Humidity", c["w_dew"], dew,
             f"dew point {_f(dew_f)}°" + (" (dry)" if dew == 1 else f" (sticky above {_f(c['dew_ok'])}°)")),
        part("wind", "Wind", c["w_wind"], wind, f"{_f(wind_mph)} mph, gusts {_f(gust_mph)}"),
    ]
    if sun is not None:
        parts.append(part("sun", "Sun", c["w_sun"], sun,
                          f"{min(100, round(100 * sun_raw))}% of a clear sky at this sun angle"))
    rows, summary = breakdown(parts, rain_capped, c["rain_cap"])
    return int(round(score)), {
        "s_temp": round(temp, 2),
        "s_dew": round(dew, 2),
        "s_wind": round(wind, 2),
        "s_sun": round(sun, 2) if sun is not None else None,
        "why": summary,
        "breakdown": rows,
        "sun_counted": sun is not None,
        "inputs": {
            "feels_f": round(feels_f, 1),
            "dew_f": round(dew_f, 1),
            "wind_mph": round(wind_mph, 1),
            "gust_mph": round(gust_mph, 1),
            "sun_pct": min(100, round(100 * sun_raw)) if sun_raw is not None else None,
            "ideal_lo": round(lo, 1),
            "ideal_hi": round(hi, 1),
        },
    }


def day_breakdown(components):
    """Average each factor's points over a day's scored hours, with a day-level reason.

    `components` are score_components() attrs for the hours that have them (forecast hours; observed
    hours only keep their score). Points average to the day's mean pre-rain-cap score.
    """
    if not components:
        return [], None
    n = len(components)
    labels = {"temp": "Temp", "dew": "Humidity", "wind": "Wind", "sun": "Sun"}
    acc = {k: {"points": 0.0, "max": 0.0, "lost": 0.0} for k in labels}
    for comp in components:
        for row in comp["breakdown"]:
            for key in ("points", "max", "lost"):
                acc[row["factor"]][key] += row[key] / n
    ins = [c["inputs"] for c in components]
    feels = [i["feels_f"] for i in ins]
    cool = sum(i["feels_f"] < i["ideal_lo"] for i in ins)
    warm = sum(i["feels_f"] > i["ideal_hi"] for i in ins)
    temp_word = (
        "in range all day"
        if not cool and not warm
        else f"too cool {cool} of {n} hours"
        if cool >= warm
        else f"too warm {warm} of {n} hours"
    )
    sun = [i["sun_pct"] for i in ins if i["sun_pct"] is not None]
    dews = [i["dew_f"] for i in ins]
    def span(vals):
        lo_, hi_ = _f(min(vals)), _f(max(vals))
        return lo_ if lo_ == hi_ else f"{lo_}–{hi_}"

    detail = {
        "temp": f"feels {span(feels)}°, {temp_word}",
        "dew": f"dew point {span(dews)}°",
        "wind": f"up to {_f(max(i['wind_mph'] for i in ins))} mph, gusts {_f(max(i['gust_mph'] for i in ins))}",
        "sun": f"{_f(sum(sun) / len(sun))}% of a clear sky on average" if sun else "",
    }
    parts = [
        {"factor": k, "label": labels[k], "points": round(v["points"], 1), "max": round(v["max"], 1),
         "lost": round(v["lost"], 1), "detail": detail[k]}
        for k, v in acc.items()
        if v["max"] > 0
    ]
    rows, summary = breakdown(parts, False, 0)
    return rows, summary


def nice_score(
    feels_f, dew_f, wind_mph, gust_mph, irr_wm2, rain_rate, sun_elev_deg, cfg=None
):
    return score_components(
        feels_f, dew_f, wind_mph, gust_mph, irr_wm2, rain_rate, sun_elev_deg, cfg
    )[0]


def forecast_rain(row):
    """A forecast hour is rainy only at >= FORECAST_RAIN_MM of blended precipitation."""
    return (row.get("precipitation") or 0) >= FORECAST_RAIN_MM


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
        # Blended precipitation is a member mean: a trace from one model is not rain.
        1.0 if forecast_rain(row) else 0.0,
        elevation(row["time"] + timedelta(minutes=30), latitude, longitude),
        cfg,
    )


def day_score(rows, day, tz, latitude, longitude, cfg=None, observed=None, now=None):
    """Mix elapsed observed hours and future forecasts; never invent missing observations."""
    c = OPTION_DEFAULTS | (cfg or {})
    hourly, rain_hours, components = [], 0, []
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
            components.append(value[1])
            rain = forecast_rain(row)
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
    factors, why = day_breakdown(components)
    if factors:
        # Forecast hours only: observed hours keep just their score.
        attrs |= {"breakdown": factors, "why": why, "breakdown_hours": len(components)}
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

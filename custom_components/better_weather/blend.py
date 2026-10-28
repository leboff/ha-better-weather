"""Pure correction, equal-weight blending and local-calendar aggregation."""

import math
from collections import Counter, defaultdict
from datetime import datetime, time
from statistics import mean

from .const import DAILY_PRECIP_CONDITION_MM, GROUP_CODES, GROUP_CONDITIONS, VARIABLES

PRECIP_GROUP_MIN = 4  # drizzle and wetter


def group(code):
    return next((i for i, codes in enumerate(GROUP_CODES) if code in codes), 2)


def voted_code(codes):
    counts = Counter(group(code) for code in codes if code is not None)
    if not counts:
        return None
    winner = max(counts, key=lambda g: (counts[g], g))
    return max(code for code in codes if code is not None and group(code) == winner)


def condition(row):
    code = row.get("weather_code")
    if code is None:
        return None
    return (
        "clear-night"
        if group(code) == 0 and row.get("is_day") == 0
        else GROUP_CONDITIONS[group(code)]
    )


def circular_mean(values):
    x = sum(math.cos(math.radians(v)) for v in values)
    y = sum(math.sin(math.radians(v)) for v in values)
    return (
        (math.degrees(math.atan2(y, x)) % 360) % 360
        if abs(x) + abs(y) > 1e-9
        else values[0]
    )


def feels_like_c(t_c, td_c, wind_kmh):
    """NWS feels-like (what weather stations report): wind chill at <=50F with wind >3 mph, heat
    index at >=80F (Rothfusz with NWS adjustments), else the air temperature. Metric in and out."""
    t = t_c * 1.8 + 32
    v = wind_kmh / 1.609344
    if t <= 50 and v > 3:
        f = 35.74 + 0.6215 * t - 35.75 * v**0.16 + 0.4275 * t * v**0.16
        return (min(f, t) - 32) / 1.8
    if t >= 80:
        rh = max(0, min(100, 100 * math.exp(17.625 * td_c / (243.04 + td_c) - 17.625 * t_c / (243.04 + t_c))))
        hi = 0.5 * (t + 61 + (t - 68) * 1.2 + rh * 0.094)
        if (hi + t) / 2 >= 80:
            hi = (
                -42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
                - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
                + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh
            )
            if rh < 13 and t <= 112:
                hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
            elif rh > 85 and t <= 87:
                hi += ((rh - 85) / 10) * ((87 - t) / 5)
        return (hi - 32) / 1.8
    return t_c


def apply_corrections(row, corrections):
    corrected = dict(row)
    for var, correction in corrections.items():
        if corrected.get(var) is not None:
            corrected[var] = (
                corrected[var] - correction["value"]
                if correction["kind"] == "offset"
                else corrected[var] * correction["value"]
            )
    t, td = corrected.get("temperature_2m"), corrected.get("dew_point_2m")
    wind = corrected.get("wind_speed_10m")
    if t is not None and td is not None and wind is not None:
        # The model's apparent temperature bakes in its own (uncorrected) wind and humidity. Rebuild
        # it from the corrected inputs, the way the station computes feels-like, so a sheltered site
        # doesn't inherit wind chill from wind it never gets.
        corrected["apparent_temperature"] = feels_like_c(t, td, wind)
    elif corrected.get("apparent_temperature") is not None:
        corrected["apparent_temperature"] -= corrections.get("temperature_2m", {}).get(
            "value", 0
        )
    if t is not None and td is not None:
        corrected["relative_humidity_2m"] = max(
            0,
            min(
                100,
                100 * math.exp(17.625 * td / (243.04 + td) - 17.625 * t / (243.04 + t)),
            ),
        )
    # Without a learned irradiance correction, use the canonical station scale.
    radiation = corrections.get("shortwave_radiation", {})
    corrected["irradiance_calibrated"] = bool(
        radiation and not radiation.get("insufficient_data", True)
    )
    return corrected


def blend_members(members, corrections=None):
    result = []
    for t in sorted(set().union(*(rows.keys() for rows in members.values()))):
        rows = [
            apply_corrections(values[t], (corrections or {}).get(model, {}))
            for model, values in members.items()
            if t in values
        ]
        temps = [
            r["temperature_2m"] for r in rows if r.get("temperature_2m") is not None
        ]
        if not temps:
            continue
        row = {"time": t, "member_count": len(temps)}
        for var in VARIABLES:
            values = [r[var] for r in rows if r.get(var) is not None]
            if not values:
                continue
            row[var] = (
                voted_code(values)
                if var == "weather_code"
                else circular_mean(values)
                if var == "wind_direction_10m"
                else mean(values)
            )
        row["model_spread_temp"] = max(temps) - min(temps)
        radiation_rows = [r for r in rows if r.get("shortwave_radiation") is not None]
        if radiation_rows:
            row["score_irradiance"] = mean(
                r["shortwave_radiation"] * (1 if r["irradiance_calibrated"] else 0.75)
                for r in radiation_rows
            )
        row["condition"] = condition(row)
        result.append(row)
    return result


def aggregate_daily(rows, tz):
    """Group UTC instants by local date, preserving 23/25-hour DST days."""
    days = defaultdict(list)
    for row in rows:
        days[row["time"].astimezone(tz).date()].append(row)
    result = []
    for day, hours in sorted(days.items()):
        entry = {"datetime": datetime.combine(day, time(), tzinfo=tz).isoformat()}
        for var, key, func in [
            ("temperature_2m", "native_temperature", max),
            ("temperature_2m", "native_templow", min),
            ("precipitation", "native_precipitation", sum),
            ("precipitation_probability", "precipitation_probability", max),
            ("wind_speed_10m", "native_wind_speed", mean),
            ("wind_gusts_10m", "native_wind_gust_speed", max),
            ("uv_index", "uv_index", max),
        ]:
            values = [h[var] for h in hours if h.get(var) is not None]
            if values:
                entry[key] = func(values)
        wet_codes = [
            h.get("weather_code")
            for h in hours
            if h.get("weather_code") is not None
            and group(h["weather_code"]) >= PRECIP_GROUP_MIN
        ]
        if entry.get("native_precipitation", 0) >= DAILY_PRECIP_CONDITION_MM and wet_codes:
            # A wet day is a wet day even if most daylight hours are dry.
            code = voted_code(wet_codes)
        else:
            daytime = [
                h.get("weather_code")
                for h in hours
                if 8 <= h["time"].astimezone(tz).hour < 20
            ]
            code = voted_code(daytime or [h.get("weather_code") for h in hours])
        entry["condition"] = condition({"weather_code": code, "is_day": 1})
        result.append(entry)
    return result

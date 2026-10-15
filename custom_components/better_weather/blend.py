"""Pure correction, equal-weight blending and local-calendar aggregation."""
from collections import Counter, defaultdict
from datetime import datetime, time
import math
from statistics import mean

from .const import GROUP_CODES, GROUP_CONDITIONS, VARIABLES


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
    return "clear-night" if group(code) == 0 and row.get("is_day") == 0 else GROUP_CONDITIONS[group(code)]


def circular_mean(values):
    x = sum(math.cos(math.radians(v)) for v in values)
    y = sum(math.sin(math.radians(v)) for v in values)
    return (math.degrees(math.atan2(y, x)) % 360) % 360 if abs(x)+abs(y) > 1e-9 else values[0]


def apply_corrections(row, corrections):
    corrected = dict(row)
    for var, correction in corrections.items():
        if corrected.get(var) is not None:
            corrected[var] = corrected[var] - correction["value"] if correction["kind"] == "offset" else corrected[var] * correction["value"]
    if corrected.get("apparent_temperature") is not None:
        corrected["apparent_temperature"] -= corrections.get("temperature_2m", {}).get("value", 0)
    t, td = corrected.get("temperature_2m"), corrected.get("dew_point_2m")
    if t is not None and td is not None:
        corrected["relative_humidity_2m"] = max(0, min(100, 100 * math.exp(17.625*td/(243.04+td) - 17.625*t/(243.04+t))))
    # Without a learned irradiance correction, use the canonical station scale.
    radiation = corrections.get("shortwave_radiation", {})
    corrected["irradiance_calibrated"] = bool(radiation and not radiation.get("insufficient_data", True))
    return corrected


def blend_members(members, corrections=None):
    result = []
    for t in sorted(set().union(*(rows.keys() for rows in members.values()))):
        rows = [apply_corrections(values[t], (corrections or {}).get(model, {}))
                for model, values in members.items() if t in values and values[t].get("temperature_2m") is not None]
        if not rows:
            continue
        row = {"time": t, "member_count": len(rows)}
        for var in VARIABLES:
            values = [r[var] for r in rows if r.get(var) is not None]
            if not values:
                continue
            row[var] = voted_code(values) if var == "weather_code" else circular_mean(values) if var == "wind_direction_10m" else mean(values)
        temps = [r["temperature_2m"] for r in rows]
        row["model_spread_temp"] = max(temps) - min(temps)
        radiation_rows = [r for r in rows if r.get("shortwave_radiation") is not None]
        if radiation_rows:
            row["score_irradiance"] = mean(r["shortwave_radiation"] * (1 if r["irradiance_calibrated"] else 0.75) for r in radiation_rows)
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
        for var, key, func in [("temperature_2m", "native_temperature", max), ("temperature_2m", "native_templow", min),
            ("precipitation", "native_precipitation", sum), ("precipitation_probability", "precipitation_probability", max),
            ("wind_speed_10m", "native_wind_speed", mean), ("wind_gusts_10m", "native_wind_gust_speed", max), ("uv_index", "uv_index", max)]:
            values = [h[var] for h in hours if h.get(var) is not None]
            if values:
                entry[key] = func(values)
        daytime = [h.get("weather_code") for h in hours if 8 <= h["time"].astimezone(tz).hour < 20]
        code = voted_code(daytime or [h.get("weather_code") for h in hours])
        entry["condition"] = condition({"weather_code": code, "is_day": 1})
        result.append(entry)
    return result

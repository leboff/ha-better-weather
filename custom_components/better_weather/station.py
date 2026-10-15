"""Station reads and conversion using Home Assistant's native unit converters."""

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_conversion import (
    PressureConverter,
    SpeedConverter,
    TemperatureConverter,
)

from .api import number
from .const import STATION_KEYS


def metric_value(value, key, unit):
    value = number(value)
    if value is None:
        return None
    try:
        if key in ("temperature", "dew_point", "feels_like"):
            return TemperatureConverter.convert(value, unit, "°C")
        if key in ("wind_speed", "wind_gust"):
            return SpeedConverter.convert(value, unit, "km/h")
        if key == "pressure":
            return PressureConverter.convert(value, unit, "hPa")
        if key == "precipitation_rate":
            return value * 25.4 if unit == "in/h" else value if unit == "mm/h" else None
        return value
    except HomeAssistantError, ValueError, TypeError:
        return None


def station_values(hass, data):
    result = {}
    for key in STATION_KEYS:
        state = hass.states.get(data.get(f"station_{key}", ""))
        if state is not None:
            result[key] = metric_value(
                state.state, key, state.attributes.get("unit_of_measurement")
            )
    return result


def metric_statistics(hass, data, stats):
    result = {}
    for key in STATION_KEYS:
        entity_id = data.get(f"station_{key}")
        state = hass.states.get(entity_id) if entity_id else None
        if state is None or entity_id not in stats:
            continue
        unit = state.attributes.get("unit_of_measurement")
        result[key] = [
            {
                **row,
                **(
                    {
                        "flat": row.get("min") is not None
                        and row.get("max") is not None
                        and abs(row["max"] - row["min"]) < 0.01
                    }
                    if key == "temperature"
                    else {}
                ),
                **{
                    s: metric_value(row.get(s), key, unit)
                    for s in ("mean", "min", "max")
                },
            }
            for row in stats[entity_id]
        ]
    return result

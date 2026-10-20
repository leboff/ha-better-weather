"""Comfort scores and correction/forecast-skill diagnostics."""

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DistanceConverter, SpeedConverter, TemperatureConverter

from .blend import aggregate_daily
from .entity import device_info

FORECAST_DAYS = 8


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            BetterWeatherSensor(entry, key)
            for key in (
                "nice_right_now",
                "nice_today",
                "nice_tomorrow",
                "corrections",
                "forecast_skill",
                "nice_rank",
                "daily_forecast",
            )
        ]
    )


class BetterWeatherSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    # Large list attributes are for dashboards, not the recorder.
    _unrecorded_attributes = frozenset(
        {"forecast", "hourly", "last_10", "models", "breakdown", "clear_sky_curve", "inputs", "top_10"}
    )

    def __init__(self, entry, key):
        super().__init__(entry.runtime_data.forecast)
        self.runtime, self.key = entry.runtime_data, key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = device_info(entry)
        if key == "daily_forecast":
            self._attr_icon = "mdi:calendar-weather"
        elif key == "nice_rank":
            self._attr_icon = "mdi:podium"
        elif key in ("corrections", "forecast_skill"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_icon = "mdi:chart-line"
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:weather-sunny"

    def daily_forecast(self):
        """Daily forecast in the user's units, shaped like weather.get_forecasts output.

        Lets dashboards read the forecast from state attributes (no service call needed).
        """
        data = self.coordinator.data or {}
        tz = self.runtime.corrections.tz
        today = dt_util.now(tz).date()
        rows = [r for r in data.get("hourly", []) if r["time"].astimezone(tz).date() >= today]
        units = self.hass.config.units
        temp_unit = units.temperature_unit
        temp_digits = 0 if temp_unit == "°F" else 1
        precip_unit = units.accumulated_precipitation_unit
        wind_unit = units.wind_speed_unit
        out = []
        for day in aggregate_daily(rows, tz)[:FORECAST_DAYS]:
            item = {"datetime": day["datetime"], "condition": day.get("condition")}
            for src, dst in (("native_temperature", "temperature"), ("native_templow", "templow")):
                if day.get(src) is not None:
                    item[dst] = round(TemperatureConverter.convert(day[src], "°C", temp_unit), temp_digits)
            if day.get("native_precipitation") is not None:
                item["precipitation"] = round(DistanceConverter.convert(day["native_precipitation"], "mm", precip_unit), 2)
            if day.get("precipitation_probability") is not None:
                item["precipitation_probability"] = round(day["precipitation_probability"])
            for src, dst in (("native_wind_speed", "wind_speed"), ("native_wind_gust_speed", "wind_gust_speed")):
                if day.get(src) is not None:
                    item[dst] = round(SpeedConverter.convert(day[src], "km/h", wind_unit), 1)
            if day.get("uv_index") is not None:
                item["uv_index"] = round(day["uv_index"], 1)
            out.append(item)
        if not out:
            return None, {}
        return out[0]["condition"], {"forecast": out}

    def value_and_attributes(self):
        skill = self.runtime.skill
        if self.key == "daily_forecast":
            return self.daily_forecast()
        if self.key == "nice_right_now":
            return skill.current()
        if self.key in ("nice_today", "nice_tomorrow"):
            day = dt_util.now(skill.tz).date() + timedelta(
                days=self.key == "nice_tomorrow"
            )
            return skill.score_day(day)
        if self.key == "nice_rank":
            # State = rank of the latest fully measured day among every measured day (1 = best).
            n, attrs = self.runtime.history.summary
            if not n:
                return None, {}
            return attrs["latest"]["rank"], attrs | {"days_ranked": n}
        if self.key == "forecast_skill":
            summary = skill.summary
            return summary["mae_30d"], {
                k: v for k, v in summary.items() if k != "mae_30d"
            }
        cc = self.runtime.corrections
        today = dt_util.now(cc.tz).date()
        frozen = sorted(
            d.isoformat()
            for d in cc.frozen
            if today - timedelta(days=cc.cfg["correction_window_days"]) <= d < today
        )
        return cc.status, dict(
            models=cc.data,
            window_days=cc.cfg["correction_window_days"],
            frozen_days_in_window=frozen,
            computed_at=cc.computed_at,
            clear_sky_curve=cc.clear_sky_curve,
        )

    @property
    def available(self):
        return self.value_and_attributes()[0] is not None

    @property
    def native_value(self):
        return self.value_and_attributes()[0]

    @property
    def extra_state_attributes(self):
        return self.value_and_attributes()[1]

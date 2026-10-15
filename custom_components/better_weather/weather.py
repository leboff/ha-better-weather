"""Station current conditions and corrected hourly/daily forecasts."""

from datetime import timedelta

from homeassistant.components.weather import (
    SingleCoordinatorWeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureDeltaConverter

from .blend import aggregate_daily
from .const import ATTRIBUTION
from .entity import device_info
from .station import station_values

HOURLY_FIELDS = {
    "temperature_2m": "native_temperature",
    "apparent_temperature": "native_apparent_temperature",
    "dew_point_2m": "native_dew_point",
    "relative_humidity_2m": "humidity",
    "precipitation": "native_precipitation",
    "precipitation_probability": "precipitation_probability",
    "wind_speed_10m": "native_wind_speed",
    "wind_gusts_10m": "native_wind_gust_speed",
    "wind_direction_10m": "wind_bearing",
    "cloud_cover": "cloud_coverage",
    "pressure_msl": "native_pressure",
    "uv_index": "uv_index",
    "is_day": "is_daytime",
    "condition": "condition",
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BetterWeather(entry)])


class BetterWeather(SingleCoordinatorWeatherEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "weather"
    _attr_name = None
    _attr_native_temperature_unit = "°C"
    _attr_native_wind_speed_unit = "km/h"
    _attr_native_pressure_unit = "hPa"
    _attr_native_precipitation_unit = "mm"
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_HOURLY | WeatherEntityFeature.FORECAST_DAILY
    )
    _attr_attribution = ATTRIBUTION

    def __init__(self, entry):
        super().__init__(entry.runtime_data.forecast)
        self.entry = entry
        self.runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_device_info = device_info(entry)

    @property
    def available(self):
        # Keep the last good forecast available through transient request failures.
        return bool(self.coordinator.data)

    def current_value(self, station_key, forecast_key):
        if not self.runtime.corrections.station_stale:
            value = station_values(self.hass, self.entry.data).get(station_key)
            if value is not None:
                return value
        return (self.runtime.skill.current_hour() or {}).get(forecast_key)

    @property
    def native_temperature(self):
        return self.current_value("temperature", "temperature_2m")

    @property
    def native_apparent_temperature(self):
        return self.current_value("feels_like", "apparent_temperature")

    @property
    def native_dew_point(self):
        return self.current_value("dew_point", "dew_point_2m")

    @property
    def humidity(self):
        return self.current_value("humidity", "relative_humidity_2m")

    @property
    def native_wind_speed(self):
        return self.current_value("wind_speed", "wind_speed_10m")

    @property
    def native_wind_gust_speed(self):
        return self.current_value("wind_gust", "wind_gusts_10m")

    @property
    def wind_bearing(self):
        return self.current_value("wind_bearing", "wind_direction_10m")

    @property
    def native_pressure(self):
        return self.current_value("pressure", "pressure_msl")

    @property
    def uv_index(self):
        return self.current_value("uv_index", "uv_index")

    @property
    def cloud_coverage(self):
        return (self.runtime.skill.current_hour() or {}).get("cloud_cover")

    @property
    def condition(self):
        rain = station_values(self.hass, self.entry.data).get("precipitation_rate")
        if not self.runtime.corrections.station_stale and rain is not None and rain > 0:
            temp = (self.runtime.skill.current_hour() or {}).get("temperature_2m")
            return "snowy" if temp is not None and temp <= 0 else "rainy"
        return (self.runtime.skill.current_hour() or {}).get("condition")

    @property
    def extra_state_attributes(self):
        row = self.runtime.skill.current_hour() or {}
        spread = row.get("model_spread_temp")
        if spread is not None:
            spread = TemperatureDeltaConverter.convert(
                spread, "°C", self._temperature_unit
            )
        return {
            "models": self.coordinator.client.models,
            "member_count": row.get("member_count", 0),
            "model_spread_temp": spread,
            "model_spread_temp_unit": self._temperature_unit,
            "station_stale": self.runtime.corrections.station_stale,
        }

    @callback
    def _async_forecast_hourly(self):
        hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        rows = [
            r
            for r in self.coordinator.data["hourly"]
            if hour <= r["time"] < hour + timedelta(hours=168)
        ]
        return [
            {
                "datetime": r["time"].isoformat(),
                **{
                    key: (bool(r[var]) if var == "is_day" else r[var])
                    for var, key in HOURLY_FIELDS.items()
                    if r.get(var) is not None
                },
            }
            for r in rows
        ]

    @callback
    def _async_forecast_daily(self):
        today = dt_util.now(self.runtime.corrections.tz).date()
        return aggregate_daily(
            [
                r
                for r in self.coordinator.data["hourly"]
                if r["time"].astimezone(self.runtime.corrections.tz).date() >= today
            ],
            self.runtime.corrections.tz,
        )[:8]

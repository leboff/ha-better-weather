"""Comfort scores and correction/forecast-skill diagnostics."""

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .entity import device_info


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
            )
        ]
    )


class BetterWeatherSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, key):
        super().__init__(entry.runtime_data.forecast)
        self.runtime, self.key = entry.runtime_data, key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = device_info(entry)
        if key in ("corrections", "forecast_skill"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_icon = "mdi:chart-line"
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_icon = "mdi:weather-sunny"

    def value_and_attributes(self):
        skill = self.runtime.skill
        if self.key == "nice_right_now":
            return skill.current()
        if self.key in ("nice_today", "nice_tomorrow"):
            day = dt_util.now(skill.tz).date() + timedelta(
                days=self.key == "nice_tomorrow"
            )
            return skill.score_day(day)
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

"""Bias-corrected, equal-weight Open-Meteo weather for Home Assistant."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from sqlalchemy.exc import SQLAlchemyError

from .api import OpenMeteoClient
from .const import DEFAULT_MODELS, STATION_KEYS
from .coordinator import CorrectionCoordinator, ForecastCoordinator
from .history import DayHistory
from .skill import SkillLog

PLATFORMS = [Platform.WEATHER, Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeData:
    forecast: ForecastCoordinator
    corrections: CorrectionCoordinator
    skill: SkillLog
    history: DayHistory


type BetterWeatherConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: BetterWeatherConfigEntry
) -> bool:
    client = OpenMeteoClient(
        async_get_clientsession(hass),
        entry.data["latitude"],
        entry.data["longitude"],
        entry.options.get("models", DEFAULT_MODELS),
    )
    corrections = CorrectionCoordinator(hass, entry, client)
    await corrections.async_config_entry_first_refresh()
    forecast = ForecastCoordinator(hass, entry, client, corrections)
    await forecast.async_config_entry_first_refresh()
    skill = SkillLog(hass, entry, forecast, corrections)
    await skill.async_load()
    history = DayHistory(hass, entry, skill)
    await history.async_load()
    entry.runtime_data = RuntimeData(forecast, corrections, skill, history)

    @callback
    def corrections_updated():
        if forecast.members:
            forecast.async_set_updated_data(forecast.blended_data())

    entry.async_on_unload(corrections.async_add_listener(corrections_updated))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    @callback
    def station_changed(event):
        skill.sample_current()
        forecast.async_update_listeners()

    sensors = [
        entry.data[f"station_{key}"]
        for key in STATION_KEYS
        if entry.data.get(f"station_{key}")
    ]
    entry.async_on_unload(
        async_track_state_change_event(hass, sensors, station_changed)
    )

    tick_running = False

    async def tick(*, recent=True):
        nonlocal tick_running
        if tick_running:
            return
        tick_running = True
        try:
            await skill.async_tick(recent=recent)
            if history.stale:
                # Never during setup: the full rescore is a few dozen recorder queries, so it starts
                # on the first scheduled tick (≤5 min after startup) in the background.
                if recent and not history.building:
                    entry.async_create_background_task(
                        hass, rebuild_history(), "Better Weather day ranking rebuild"
                    )
            else:
                await history.async_tick()
            forecast.async_update_listeners()
        except (
            HomeAssistantError,
            SQLAlchemyError,
            OSError,
            ValueError,
            TypeError,
        ) as err:
            _LOGGER.warning("Station history/skill refresh failed: %s", err)
        finally:
            tick_running = False

    async def rebuild_history():
        try:
            await history.async_rebuild()
            forecast.async_update_listeners()
        except (HomeAssistantError, SQLAlchemyError, OSError, ValueError, TypeError) as err:
            _LOGGER.warning("Day ranking rebuild failed (retries next tick): %s", err)
        except RuntimeError as err:  # recorder executor gone: HA is stopping mid-rebuild
            _LOGGER.debug("Day ranking rebuild stopped: %s", err)

    @callback
    def scheduled_tick(now):
        entry.async_create_background_task(
            hass, tick(), "Better Weather comfort and skill"
        )

    entry.async_on_unload(
        async_track_time_interval(hass, scheduled_tick, timedelta(minutes=5))
    )
    # The exact local 20:00 snapshot is independent of the five-minute cadence.
    entry.async_on_unload(
        async_track_time_change(hass, scheduled_tick, hour=20, minute=0, second=0)
    )
    entry.async_on_unload(
        async_track_time_change(
            hass,
            scheduled_tick,
            hour=int(skill.cfg["nice_sleep_hour"]) % 24,
            minute=0,
            second=0,
        )
    )
    await tick(recent=False)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: BetterWeatherConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: BetterWeatherConfigEntry
) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.skill.async_close()
        return True
    return False

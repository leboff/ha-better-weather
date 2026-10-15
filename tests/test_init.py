from datetime import timedelta

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed


async def test_unload_cleans_up(hass, setup_entry, freezer, statistics):
    old = setup_entry.runtime_data
    assert old.forecast._listeners
    assert old.corrections._listeners
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert not old.forecast._listeners
    assert not old.corrections._listeners
    before = statistics.call_count
    samples = str(old.skill.samples)
    hass.states.async_set(
        "sensor.league_temperature", 99, {"unit_of_measurement": "°F"}
    )
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert statistics.call_count == before
    assert str(old.skill.samples) == samples
    assert hass.states.get("weather.better_weather").state == "unavailable"


async def test_reload_does_not_leak_listeners(hass, setup_entry):
    old = setup_entry.runtime_data
    listeners = hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0)
    old_forecast_listeners = len(old.forecast._listeners)
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    new = setup_entry.runtime_data
    assert old is not new
    assert not old.forecast._listeners
    assert not old.corrections._listeners
    assert len(new.forecast._listeners) == old_forecast_listeners
    assert hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0) == listeners
    assert hass.states.get("weather.better_weather").attributes["temperature"] == 68

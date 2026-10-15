from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.better_weather.api import FORECAST_URL


async def test_station_current_and_forecasts(hass, setup_entry):
    state = hass.states.get("weather.better_weather")
    assert state is not None
    assert state.attributes["temperature"] == 68
    assert state.attributes["temperature_unit"] == "°F"
    assert state.state in (
        "sunny",
        "clear-night",
        "cloudy",
        "partlycloudy",
        "fog",
        "rainy",
        "snowy",
        "lightning-rainy",
    )
    for forecast_type in ("hourly", "daily"):
        result = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": state.entity_id, "type": forecast_type},
            blocking=True,
            return_response=True,
        )
        rows = result[state.entity_id]["forecast"]
        assert rows
        if forecast_type == "daily":
            assert all(row["temperature"] >= row["templow"] for row in rows)
        else:
            assert len(rows) == 168
            assert rows[0]["datetime"].startswith("2026-10-15T16:00")
    assert setup_entry.runtime_data.forecast.last_fetch


async def test_station_events_and_rain(hass, setup_entry):
    hass.states.async_set(
        "sensor.league_temperature", 32, {"unit_of_measurement": "°F"}
    )
    hass.states.async_set(
        "sensor.league_precipitation_intensity", 0.1, {"unit_of_measurement": "in/h"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("weather.better_weather")
    assert state.attributes["temperature"] == 32
    assert state.state == "rainy"
    row = setup_entry.runtime_data.skill.current_hour()
    row["temperature_2m"] = -1
    setup_entry.runtime_data.forecast.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(state.entity_id).state == "snowy"
    hass.states.async_set(
        "sensor.league_temperature", "unavailable", {"unit_of_measurement": "°F"}
    )
    await hass.async_block_till_done()
    row = setup_entry.runtime_data.skill.current_hour()
    assert hass.states.get(state.entity_id).attributes["temperature"] == pytest.approx(
        round(row["temperature_2m"] * 1.8 + 32), abs=0.1
    )


async def test_frozen_falls_back(hass, setup_entry, statistics):
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    rows = [
        dict(start=(now - timedelta(hours=h)).timestamp(), min=68, max=68, mean=68)
        for h in (3, 2, 1)
    ]
    statistics.return_value = {"sensor.league_temperature": rows}
    await setup_entry.runtime_data.skill.async_tick()
    setup_entry.runtime_data.forecast.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get("weather.better_weather")
    row = setup_entry.runtime_data.skill.current_hour()
    assert setup_entry.runtime_data.corrections.station_stale
    assert state.attributes["temperature"] == pytest.approx(
        round(row["temperature_2m"] * 1.8 + 32), abs=0.1
    )
    assert (
        hass.states.get("sensor.better_weather_nice_right_now").attributes["source"]
        == "forecast"
    )


async def test_failure_retains_last_data(hass, setup_entry, http):
    coordinator = setup_entry.runtime_data.forecast
    previous = coordinator.data
    http.clear_requests()
    http.get(FORECAST_URL, status=500)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert coordinator.data is previous
    assert hass.states.get("weather.better_weather").state != "unavailable"

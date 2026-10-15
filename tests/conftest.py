"""Real fixture loaders and HA custom integration support."""

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_weather.api import FORECAST_URL, PREVIOUS_URL

pytest_plugins = "pytest_homeassistant_custom_component"


def fixture_json(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


STATION_DATA = {
    "name": "Better Weather",
    "latitude": 39.93999,
    "longitude": -75.18101,
    **{
        f"station_{key}": f"sensor.league_{key}"
        for key in (
            "temperature",
            "feels_like",
            "dew_point",
            "wind_speed",
            "wind_gust",
            "irradiance",
        )
    },
    "station_precipitation_rate": "sensor.league_precipitation_intensity",
}


@pytest.fixture
async def station(hass):
    await hass.config.async_set_time_zone("America/New_York")
    hass.config.units = US_CUSTOMARY_SYSTEM
    values = {
        "temperature": (68, "°F"),
        "feels_like": (67, "°F"),
        "dew_point": (45, "°F"),
        "wind_speed": (4, "mph"),
        "wind_gust": (8, "mph"),
        "irradiance": (500, "W/m²"),
        "precipitation_intensity": (0, "in/h"),
    }
    for key, (value, unit) in values.items():
        hass.states.async_set(
            f"sensor.league_{key}", value, {"unit_of_measurement": unit}
        )
    return values


@pytest.fixture
def http(aioclient_mock):
    aioclient_mock.get(FORECAST_URL, json=fixture_json("openmeteo_forecast.json"))
    aioclient_mock.get(PREVIOUS_URL, json=fixture_json("openmeteo_previous_runs.json"))
    return aioclient_mock


@pytest.fixture
def statistics():
    with patch(
        "custom_components.better_weather.coordinator.statistics_during_period",
        return_value=fixture_json("station_statistics.json"),
    ) as mocked:
        yield mocked


@pytest.fixture
async def setup_entry(hass, station, http, statistics, freezer, recorder_mock):
    freezer.move_to("2026-10-15T16:00:00Z")
    entry = MockConfigEntry(
        domain="better_weather",
        title="Better Weather",
        data=STATION_DATA,
        unique_id="39.94_-75.181",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url):
    """Initialize the recorder test database before HA's fixture is constructed."""
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    yield

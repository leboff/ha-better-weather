from datetime import date

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.better_weather.api import (
    FORECAST_URL,
    OpenMeteoClient,
    parse_members,
)
from custom_components.better_weather.const import DEFAULT_MODELS, VARIABLES


async def test_request_parameters(hass, http):
    client = OpenMeteoClient(async_get_clientsession(hass), 12, 34, DEFAULT_MODELS)
    await client.forecast()
    query = http.mock_calls[-1][1].query
    assert query["latitude"] == "12"
    assert query["longitude"] == "34"
    assert query["models"] == ",".join(DEFAULT_MODELS)
    assert query["hourly"] == ",".join(VARIABLES)
    assert query["forecast_days"] == "8"
    assert query["timezone"] == "UTC"
    assert query["wind_speed_unit"] == "kmh"
    await client.previous_runs(date(2026, 10, 15), 60)
    query = http.mock_calls[-1][1].query
    assert query["start_date"] == "2026-08-15"
    assert query["end_date"] == "2026-10-14"
    assert all(v.endswith("_previous_day1") for v in query["hourly"].split(","))


@pytest.mark.parametrize(
    "payload", [[], {}, {"hourly": None}, {"hourly": {"time": []}}]
)
async def test_malformed_response(hass, aioclient_mock, payload):
    aioclient_mock.get(FORECAST_URL, json=payload)
    client = OpenMeteoClient(async_get_clientsession(hass), 12, 34, DEFAULT_MODELS)
    with pytest.raises(UpdateFailed):
        await client.forecast()


async def test_timeout(hass, aioclient_mock):
    aioclient_mock.get(FORECAST_URL, exc=TimeoutError())
    client = OpenMeteoClient(async_get_clientsession(hass), 12, 34, DEFAULT_MODELS)
    with pytest.raises(UpdateFailed):
        await client.forecast()


def test_single_model_and_nonfinite():
    members = parse_members(
        {
            "hourly": {
                "time": ["2026-10-15T00:00"],
                "temperature_2m": [10],
                "wind_speed_10m": [float("nan")],
            }
        },
        ["a"],
    )
    row = next(iter(members["a"].values()))
    assert row["temperature_2m"] == 10
    assert row["wind_speed_10m"] is None

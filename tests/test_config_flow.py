from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_weather.api import FORECAST_URL
from custom_components.better_weather.const import DEFAULT_MODELS, OPTION_DEFAULTS

from .conftest import STATION_DATA

pytestmark = pytest.mark.usefixtures("recorder_mock")


async def test_user_flow(hass, http, station):
    with patch("custom_components.better_weather.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            "better_weather", context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], STATION_DATA
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == STATION_DATA
    assert result["result"].unique_id == "39.94_-75.181"


async def test_duplicate(hass, http):
    entry = MockConfigEntry(
        domain="better_weather", unique_id="39.94_-75.181", data=STATION_DATA
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        "better_weather", context={"source": "user"}, data=STATION_DATA
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not http.mock_calls


async def test_cannot_connect(hass, aioclient_mock):
    aioclient_mock.get(FORECAST_URL, status=503)
    result = await hass.config_entries.flow.async_init(
        "better_weather", context={"source": "user"}, data=STATION_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_reload(hass, setup_entry):
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    options = OPTION_DEFAULTS | {
        "models": DEFAULT_MODELS[:2],
        "correction_window_days": 30,
    }
    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], options
        )
        await hass.async_block_till_done()
        assert reload.call_count == 1
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options["models"] == DEFAULT_MODELS[:2]
    assert setup_entry.options["correction_window_days"] == 30


async def test_options_validation(hass, setup_entry):
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], OPTION_DEFAULTS | {"models": []}
    )
    assert result["errors"]["models"] == "no_models"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], OPTION_DEFAULTS | {"dew_bad": 10}
    )
    assert result["errors"]["base"] == "invalid_thresholds"


async def test_reconfigure(hass, setup_entry):
    result = await hass.config_entries.flow.async_init(
        "better_weather",
        context={"source": "reconfigure", "entry_id": setup_entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"station_temperature": "sensor.new_temperature"}
        )
        await hass.async_block_till_done()
        assert reload.call_count == 1
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.data["station_temperature"] == "sensor.new_temperature"
    assert "station_dew_point" not in setup_entry.data

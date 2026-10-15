from custom_components.better_weather.api import FORECAST_URL, PREVIOUS_URL

from .conftest import fixture_json


async def test_correction_failure_keeps_last_good(hass, setup_entry, http, caplog):
    cc = setup_entry.runtime_data.corrections
    old = cc.data
    http.clear_requests()
    http.get(PREVIOUS_URL, status=500)
    await cc.async_refresh()
    assert cc.status == "error"
    assert cc.data == old
    count = caplog.text.count("Correction refresh failed")
    await cc.async_refresh()
    assert caplog.text.count("Correction refresh failed") == count == 1


async def test_disabled_corrections_still_guard(hass, setup_entry):
    entry = setup_entry
    hass.config_entries.async_update_entry(entry, options={"correction_enabled": False})
    await hass.async_block_till_done()
    cc = entry.runtime_data.corrections
    assert cc.status == "disabled"
    for variables in cc.data.values():
        assert all(
            v["value"] == (0 if v["kind"] == "offset" else 1)
            for v in variables.values()
        )
    assert cc.frozen


async def test_correction_first_failure_nonfatal(hass, setup_entry, http):
    entry = setup_entry
    assert await hass.config_entries.async_unload(entry.entry_id)
    http.clear_requests()
    http.get(PREVIOUS_URL, status=503)
    http.get(FORECAST_URL, json=fixture_json("openmeteo_forecast.json"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.corrections.status == "error"
    assert hass.states.get("weather.better_weather").state != "unavailable"


async def test_recorder_runs_in_executor(hass, setup_entry, statistics):
    import threading
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    main_thread = threading.get_ident()
    calls = []

    def query(*args):
        assert threading.get_ident() != main_thread
        calls.append(args)
        return fixture_json("station_statistics.json")

    statistics.side_effect = query
    now = dt_util.utcnow()
    await setup_entry.runtime_data.corrections.async_station_statistics(
        now - timedelta(hours=4), now
    )
    assert len(calls) == 1
    assert calls[0][4:] == ("hour", None, {"mean", "min", "max"})
    assert calls[0][1].utcoffset() == timedelta(0)
    assert "sensor.league_temperature" in calls[0][3]

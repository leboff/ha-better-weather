from datetime import UTC, date, datetime, timedelta
from statistics import mean

import pytest
from homeassistant.util import dt as dt_util

from custom_components.better_weather.nice import nice_score
from custom_components.better_weather.skill import SkillLog, skill_summary
from custom_components.better_weather.solar import elevation

from .conftest import fixture_json


async def test_right_now(hass, setup_entry):
    skill = setup_entry.runtime_data.skill
    expected = nice_score(
        67,
        45,
        4,
        8,
        500,
        0,
        elevation(dt_util.utcnow(), skill.latitude, skill.longitude),
    )
    state = hass.states.get("sensor.better_weather_nice_right_now")
    assert float(state.state) == expected
    assert state.attributes["source"] == "station"
    for key in ("s_temp", "s_dew", "s_wind", "s_sun"):
        assert 0 <= state.attributes[key] <= 1
    hass.states.async_set("sensor.league_feels_like", 95, {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    assert float(hass.states.get(state.entity_id).state) < expected
    assert setup_entry.runtime_data.skill.samples


async def test_tomorrow_rain_cap(hass, setup_entry):
    runtime = setup_entry.runtime_data
    tomorrow = date(2026, 10, 16)
    row = next(
        r
        for r in runtime.forecast.data["hourly"]
        if r["time"].astimezone(runtime.skill.tz).date() == tomorrow
        and r["time"].astimezone(runtime.skill.tz).hour == 12
    )
    row["precipitation"] = 5
    runtime.forecast.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.better_weather_nice_tomorrow")
    assert state.state != "unavailable"
    assert float(state.state) <= 35
    assert state.attributes["rain_hours"] >= 1
    assert all(h["source"] == "forecast" for h in state.attributes["hourly"])
    wet = next(
        h for h in state.attributes["hourly"] if h["hour"] == row["time"].isoformat()
    )
    assert wet["score"] <= 10
    assert state.attributes["high_f"] >= state.attributes["low_f"]
    assert state.attributes["dew_point_avg_f"] is not None


async def test_corrections_and_diagnostics(hass, setup_entry):
    state = hass.states.get("sensor.better_weather_corrections")
    # ECMWF never publishes previous-run gusts: flagged unsupported, not a data gap.
    assert state.state == "ok"
    gust = state.attributes["models"]["ecmwf_ifs025"]["wind_gusts_10m"]
    assert gust["unsupported"] and gust["value"] == 1
    assert state.attributes["window_days"] == 60
    assert (
        state.attributes["frozen_days_in_window"]
        == fixture_json("golden_corrections.json")["frozen_days"]
    )
    assert state.attributes["models"]["gem_seamless"]["temperature_2m"][
        "value"
    ] == pytest.approx(0.3049, abs=0.002)
    from custom_components.better_weather.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    data = await async_get_config_entry_diagnostics(hass, setup_entry)
    assert data["last_fetch"]
    assert "latitude" not in str(data)
    assert "sensor.league" not in str(data)


async def test_snapshot_finalize_persist(hass, setup_entry, freezer, statistics):
    runtime = setup_entry.runtime_data
    log = runtime.skill
    freezer.move_to("2026-10-16T00:00:00Z")  # local 20:00, snapshot tomorrow.
    await log.async_tick(recent=False)
    record = dict(log.records["2026-10-16"])
    assert record["forecast"] is not None
    assert record["raw_forecast"] is not None
    await log.async_snapshot(date(2026, 10, 16))
    assert log.records["2026-10-16"] == record
    # Move the real, non-frozen October 14 observations to the target day.
    shifted = {}
    for sid, rows in fixture_json("station_statistics.json").items():
        shifted[sid] = [
            {**r, "start": r["start"] + 2 * 86400, "end": r["end"] + 2 * 86400}
            for r in rows
            if datetime.fromtimestamp(r["start"], log.tz).date() == date(2026, 10, 14)
        ]
    statistics.return_value = shifted
    freezer.move_to("2026-10-17T01:00:00Z")  # local 21:00, full recorder hours ready.
    await log.async_tick()
    finalized = log.records["2026-10-16"]
    assert finalized["observed"] is not None
    # Independently evaluate canonical scores from the unconverted fixture values.
    indexed = {sid: {r["start"]: r for r in rows} for sid, rows in shifted.items()}
    scores, rain = [], False
    for r in shifted["sensor.league_temperature"]:
        t = datetime.fromtimestamp(r["start"], UTC)
        if not 8 <= t.astimezone(log.tz).hour < 20:
            continue

        def val(k, stat="mean"):
            return indexed[f"sensor.league_{k}"][r["start"]][stat]

        wet = val("precipitation_intensity") >= 0.01
        score = nice_score(
            val("feels_like"),
            val("dew_point"),
            val("wind_speed"),
            val("wind_gust", "max"),
            val("irradiance"),
            val("precipitation_intensity"),
            elevation(t + timedelta(minutes=30), log.latitude, log.longitude),
            log.score_cfg,  # observed hours use the station's learned clear-sky curve
        )
        scores.append(min(score, 10) if wet else score)
        rain |= wet
    expected = round(min(mean(scores), 35) if rain else mean(scores), 1)
    assert finalized["observed"] == expected
    runtime.forecast.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.better_weather_forecast_skill")
    assert float(state.state) == round(abs(record["forecast"] - expected), 1)
    assert state.attributes["n_days"] == 1
    assert state.attributes["raw_mae_30d"] == round(
        abs(record["raw_forecast"] - expected), 1
    )
    reloaded = SkillLog(hass, setup_entry, runtime.forecast, runtime.corrections)
    await reloaded.async_load()
    assert reloaded.records == log.records


async def test_today_uses_persisted_elapsed_samples(hass, setup_entry, freezer):
    log = setup_entry.runtime_data.skill
    freezer.move_to("2026-10-15T13:00:00Z")  # local 09:00
    log.samples["2026-10-15T12:00:00+00:00"] = {
        "scores": [90, 100],
        "rain_rates": [0, 0],
    }
    value, attrs = log.score_day(date(2026, 10, 15))
    assert value is not None
    assert attrs["observed_hours"] == 1
    assert attrs["forecast_hours"] == 11
    assert attrs["hourly"][0]["score"] == 95
    assert attrs["hourly"][0]["source"] == "station"


def test_skill_metrics_window_and_precision():
    records = {
        "2026-10-14": dict(
            forecast=90,
            raw_forecast=80,
            observed=80,
            high_f=70,
            observed_high_f=72,
            predicted_rain=False,
            observed_rain=True,
        ),
        "2026-10-15": dict(
            forecast=95,
            raw_forecast=90,
            observed=90,
            high_f=70,
            observed_high_f=70,
            predicted_rain=True,
            observed_rain=True,
        ),
        "2026-08-01": dict(forecast=0, observed=100),
    }
    summary = skill_summary(records, date(2026, 10, 15))
    assert summary["mae_30d"] == 7.5
    assert summary["raw_mae_30d"] == 0
    assert summary["high_temp_mae_f"] == 1
    assert summary["rain_hit_rate"] == 0.5
    assert summary["nice_day_precision"] == 0.5
    assert summary["nice_day_recall"] == 1


async def test_frozen_skill_days_excluded(hass, setup_entry):
    log = setup_entry.runtime_data.skill
    target = date(2026, 10, 14)
    log.records[str(target)] = dict(forecast=90, raw_forecast=80, observed=70)
    assert log.summary["n_days"] == 1
    # A run detected later must also invalidate a previously graded date.
    log.corrections.frozen.add(target)
    await log.async_finalize(target)
    assert log.records[str(target)]["skipped"] == "frozen_station"
    assert log.summary["n_days"] == 0


async def test_incomplete_observations_not_graded(hass, setup_entry):
    log = setup_entry.runtime_data.skill
    target = date(2026, 10, 14)
    log.records[str(target)] = dict(forecast=90, raw_forecast=80)
    from unittest.mock import AsyncMock, patch

    with patch.object(
        log.corrections, "async_station_statistics", AsyncMock(return_value={})
    ):
        await log.async_finalize(target)
    assert log.records[str(target)].get("observed") is None


async def test_skill_retention(hass, setup_entry):
    log = setup_entry.runtime_data.skill
    start = date(2025, 1, 1)
    log.records = {str(start + timedelta(days=i)): {"forecast": 50} for i in range(405)}
    await log.async_save()
    assert len(log.records) == 400
    assert str(start + timedelta(days=5)) == min(log.records)


async def test_missing_input_falls_back(hass, setup_entry):
    hass.states.async_set(
        "sensor.league_precipitation_intensity",
        "unavailable",
        {"unit_of_measurement": "in/h"},
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.better_weather_nice_right_now").attributes["source"]
        == "forecast"
    )
    setup_entry.runtime_data.forecast.data["hourly"] = []
    setup_entry.runtime_data.forecast.async_update_listeners()
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.better_weather_nice_right_now").state == "unavailable"
    )


async def test_scheduled_snapshot_and_startup_catchup(hass, setup_entry, freezer):
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    log = setup_entry.runtime_data.skill
    assert "2026-10-16" not in log.records
    freezer.move_to("2026-10-16T00:00:00Z")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "2026-10-16" in log.records
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    log.records = {}
    await log.async_save()
    # A startup later that evening recovers the missed tomorrow snapshot.
    freezer.move_to("2026-10-16T01:00:00Z")
    assert await hass.config_entries.async_setup(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert "2026-10-16" in setup_entry.runtime_data.skill.records


async def test_daily_forecast_sensor_matches_weather_service(hass, setup_entry):
    """The attribute forecast is a drop-in for template forecast sensors (user units)."""
    state = hass.states.get("sensor.better_weather_daily_forecast")
    assert state is not None and state.state not in ("unavailable", "unknown")
    fc = state.attributes["forecast"]
    assert 1 <= len(fc) <= 8
    resp = await hass.services.async_call(
        "weather",
        "get_forecasts",
        {"entity_id": "weather.better_weather", "type": "daily"},
        blocking=True,
        return_response=True,
    )
    svc = resp["weather.better_weather"]["forecast"]
    by_day = {f["datetime"]: f for f in svc}
    for day in fc:
        ref = by_day[day["datetime"]]
        assert day["condition"] == ref["condition"]
        assert abs(day["temperature"] - ref["temperature"]) <= 0.51
        assert abs(day["templow"] - ref["templow"]) <= 0.51
        assert day["temperature"] >= day["templow"]
    assert state.state == fc[0]["condition"]


async def test_right_now_explains_itself(hass, setup_entry):
    state = hass.states.get("sensor.better_weather_nice_right_now")
    rows = state.attributes["breakdown"]
    assert {r["factor"] for r in rows} >= {"temp", "dew", "wind"}
    # Points add up to the score (before rounding / rain cap) and each factor's max to 100.
    assert sum(r["max"] for r in rows) == pytest.approx(100, abs=0.3)
    assert sum(r["points"] for r in rows) == pytest.approx(float(state.state), abs=0.6)
    assert [r["lost"] for r in rows] == sorted((r["lost"] for r in rows), reverse=True)
    assert isinstance(state.attributes["why"], str) and state.attributes["why"]


async def test_clear_sky_curve_learned(hass, setup_entry):
    runtime = setup_entry.runtime_data
    curve = runtime.corrections.clear_sky_curve
    assert len(curve) >= 3
    assert [e for e, _ in curve] == sorted(e for e, _ in curve)
    assert all(0.3 <= f <= 1 for _, f in curve)
    state = hass.states.get("sensor.better_weather_corrections")
    assert state.attributes["clear_sky_curve"] == curve


async def test_tomorrow_explains_itself(hass, setup_entry):
    state = hass.states.get("sensor.better_weather_nice_tomorrow")
    rows = state.attributes["breakdown"]
    assert state.attributes["breakdown_hours"] == len(state.attributes["hourly"])
    assert {r["factor"] for r in rows} >= {"temp", "dew", "wind"}
    assert sum(r["max"] for r in rows) == pytest.approx(100, abs=0.5)
    if not state.attributes["rain_hours"]:
        assert sum(r["points"] for r in rows) == pytest.approx(float(state.state), abs=1)
    assert all(r["detail"] for r in rows)
    assert state.attributes["why"]


async def test_today_explains_measured_hours(hass, setup_entry, freezer):
    """Today's elapsed hours are measured, and they still say why (from the hour's mean conditions)."""
    log = setup_entry.runtime_data.skill
    # Real, non-frozen Oct 14 station hours moved onto Oct 15.
    keys = {v: k.removeprefix("station_") for k, v in log.entry.data.items() if k.startswith("station_")}
    shifted = {}
    for sid, rows in fixture_json("station_statistics.json").items():
        if sid not in keys:
            continue
        shifted[keys[sid]] = [
            {**r, "start": r["start"] + 86400, "end": r["end"] + 86400}
            for r in rows
            if datetime.fromtimestamp(r["start"], log.tz).date() == date(2026, 10, 14)
        ]
    log.corrections.stats = shifted
    log.samples = {}
    freezer.move_to("2026-10-15T20:00:00Z")  # local 16:00: 8 measured hours, 4 forecast
    value, attrs = log.score_day(date(2026, 10, 15))
    assert attrs["observed_hours"] == 8 and attrs["forecast_hours"] == 4
    assert attrs["breakdown_hours"] == 12
    rows = attrs["breakdown"]
    assert {r["factor"] for r in rows} >= {"temp", "dew", "wind"}
    assert sum(r["max"] for r in rows) == pytest.approx(100, abs=0.5)
    if not attrs["rain_hours"]:
        assert sum(r["points"] for r in rows) == pytest.approx(value, abs=1)
    assert attrs["why"]
    # A live sample mean overrides the hour's score but keeps its explanation.
    hour = "2026-10-15T12:00:00+00:00"
    log.samples[hour] = {"scores": [42], "rain_rates": [0]}
    obs = log.observed_hours(explain=True)
    assert obs[hour]["score"] == 42 and obs[hour]["components"]["breakdown"]
    assert "components" not in log.observed_hours()[hour]

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from custom_components.better_weather.api import parse_members
from custom_components.better_weather.blend import aggregate_daily, apply_corrections, blend_members, voted_code


def test_mean_nulls_circular_vote():
    t = datetime(2026, 10, 15, tzinfo=UTC)
    members = {"a": {t: {"temperature_2m": 10, "wind_direction_10m": 350, "weather_code": 61}},
               "b": {t: {"temperature_2m": 20, "wind_direction_10m": 10, "weather_code": 63}},
               "c": {t: {"temperature_2m": None, "wind_direction_10m": None}}}
    row = blend_members(members)[0]
    assert row["temperature_2m"] == 15
    assert min(row["wind_direction_10m"], 360-row["wind_direction_10m"]) == pytest.approx(0)
    assert row["member_count"] == 2
    assert row["condition"] == "rainy"
    assert voted_code([0, 3, 95]) == 95
    assert voted_code([1, 2, 95]) == 2


def test_rh_recomputed_and_apparent_offset():
    row = apply_corrections(dict(temperature_2m=20, dew_point_2m=10, apparent_temperature=21, relative_humidity_2m=1),
                            {"temperature_2m": dict(kind="offset", value=2)})
    assert row["temperature_2m"] == 18
    assert row["apparent_temperature"] == 19
    assert row["relative_humidity_2m"] == pytest.approx(59.548, abs=0.1)


def test_preceding_hour_alignment():
    members = parse_members({"hourly": {"time": ["2026-10-15T00:00", "2026-10-15T01:00"],
            "temperature_2m_a": [10, 11], "precipitation_a": [1, 2], "shortwave_radiation_a": [100, 200]}}, ["a"])
    row = members["a"][datetime(2026, 10, 15, tzinfo=UTC)]
    assert row["temperature_2m"] == 10
    assert row["precipitation"] == 2
    assert row["shortwave_radiation"] == 200


def test_local_daily_fall_back():
    tz = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 4, tzinfo=UTC)
    rows = [dict(time=start+timedelta(hours=i), temperature_2m=i, precipitation=1, weather_code=0) for i in range(49)]
    days = aggregate_daily(rows, tz)
    assert days[0]["native_precipitation"] == 25
    assert days[1]["native_precipitation"] == 24
    assert days[0]["native_temperature"] == 24
    assert days[0]["native_templow"] == 0
    assert days[1]["datetime"].startswith("2026-11-02T00:00:00-05:00")

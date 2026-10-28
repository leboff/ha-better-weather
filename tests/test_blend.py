from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.better_weather.api import parse_members
from custom_components.better_weather.blend import (
    feels_like_c,
    aggregate_daily,
    apply_corrections,
    blend_members,
    voted_code,
)


def test_mean_nulls_circular_vote():
    t = datetime(2026, 10, 15, tzinfo=UTC)
    members = {
        "a": {t: {"temperature_2m": 10, "wind_direction_10m": 350, "weather_code": 61}},
        "b": {t: {"temperature_2m": 20, "wind_direction_10m": 10, "weather_code": 63}},
        "c": {t: {"temperature_2m": None, "wind_direction_10m": None}},
    }
    row = blend_members(members)[0]
    assert row["temperature_2m"] == 15
    assert min(
        row["wind_direction_10m"], 360 - row["wind_direction_10m"]
    ) == pytest.approx(0)
    assert row["member_count"] == 2
    assert row["condition"] == "rainy"
    assert voted_code([0, 3, 95]) == 95
    assert voted_code([1, 2, 95]) == 2


def test_rh_recomputed_and_apparent_offset():
    # Without wind, apparent temperature can't be rebuilt, so it only gets the temperature offset.
    row = apply_corrections(
        dict(
            temperature_2m=20,
            dew_point_2m=10,
            apparent_temperature=21,
            relative_humidity_2m=1,
        ),
        {"temperature_2m": dict(kind="offset", value=2)},
    )
    assert row["temperature_2m"] == 18
    assert row["apparent_temperature"] == 19
    assert row["relative_humidity_2m"] == pytest.approx(59.548, abs=0.1)


def test_apparent_rebuilt_from_corrected_wind():
    # 65F with 6 mph model wind: the model says it feels 3F cooler. A sheltered station
    # (wind ratio 0.4) never gets that chill, and feels-like is just the air temperature.
    raw = dict(
        temperature_2m=(65.6 - 32) / 1.8,
        dew_point_2m=(42.5 - 32) / 1.8,
        apparent_temperature=(62.7 - 32) / 1.8,
        wind_speed_10m=6 * 1.609344,
    )
    row = apply_corrections(raw, {"wind_speed_10m": dict(kind="ratio", value=0.4)})
    assert row["apparent_temperature"] == pytest.approx(row["temperature_2m"])


def test_feels_like_matches_nws():
    def f(t_f, td_f, mph):
        return feels_like_c((t_f - 32) / 1.8, (td_f - 32) / 1.8, mph * 1.609344) * 1.8 + 32

    assert f(40, 30, 10) == pytest.approx(33.6, abs=0.2)  # NWS wind chill chart
    assert f(40, 30, 2) == pytest.approx(40)  # calm: no chill
    assert f(90, 72.6, 5) == pytest.approx(98.0, abs=1.0)  # ~55% RH heat index ~98F
    assert f(70, 60, 15) == pytest.approx(70)  # in between: air temperature


def test_preceding_hour_alignment():
    members = parse_members(
        {
            "hourly": {
                "time": ["2026-10-15T00:00", "2026-10-15T01:00"],
                "temperature_2m_a": [10, 11],
                "precipitation_a": [1, 2],
                "shortwave_radiation_a": [100, 200],
            }
        },
        ["a"],
    )
    row = members["a"][datetime(2026, 10, 15, tzinfo=UTC)]
    assert row["temperature_2m"] == 10
    assert row["precipitation"] == 2
    assert row["shortwave_radiation"] == 200


def test_local_daily_fall_back():
    tz = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 4, tzinfo=UTC)
    rows = [
        dict(
            time=start + timedelta(hours=i),
            temperature_2m=i,
            precipitation=1,
            weather_code=0,
        )
        for i in range(49)
    ]
    days = aggregate_daily(rows, tz)
    assert days[0]["native_precipitation"] == 25
    assert days[1]["native_precipitation"] == 24
    assert days[0]["native_temperature"] == 24
    assert days[0]["native_templow"] == 0
    assert days[1]["datetime"].startswith("2026-11-02T00:00:00-05:00")


def test_variable_members_are_independent():
    t = datetime(2026, 10, 15, tzinfo=UTC)
    result = blend_members(
        {
            "a": {t: dict(temperature_2m=10, precipitation=2)},
            "b": {t: dict(temperature_2m=None, precipitation=4)},
        }
    )
    assert result[0]["temperature_2m"] == 10
    assert result[0]["precipitation"] == 3
    assert result[0]["member_count"] == 1


def test_daily_condition_wet_day_with_mostly_dry_daylight():
    """0.43 in of evening rain must not render as 'cloudy' just because daylight was dry."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    start = datetime(2026, 10, 16, 4, tzinfo=UTC)  # 00:00 local
    rows = []
    for i in range(24):
        wet = i in (19, 20)
        rows.append(
            dict(
                time=start + timedelta(hours=i),
                temperature_2m=20,
                weather_code=61 if wet else 3,
                precipitation=5.0 if wet else 0.0,
            )
        )
    (day,) = aggregate_daily(rows, tz)
    assert day["condition"] == "rainy"
    dry = [dict(r, precipitation=0.0, weather_code=3) for r in rows]
    assert aggregate_daily(dry, tz)[0]["condition"] == "cloudy"


def test_weather_code_shifted_with_precipitation():
    """weather_code describes the preceding hour, like precipitation, so they stay aligned."""
    from custom_components.better_weather.api import parse_members

    payload = {
        "hourly": {
            "time": ["2026-10-16T19:00", "2026-10-16T20:00"],
            "weather_code_gem_seamless": [3, 61],
            "precipitation_gem_seamless": [0.0, 4.0],
            "temperature_2m_gem_seamless": [20, 20],
        }
    }
    rows = parse_members(payload, ["gem_seamless"])["gem_seamless"]
    (hour19,) = [r for t, r in rows.items() if t.hour == 19]
    assert hour19["weather_code"] == 61 and hour19["precipitation"] == 4.0

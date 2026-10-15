from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.better_weather.api import parse_members
from custom_components.better_weather.const import DEFAULT_MODELS
from custom_components.better_weather.correction import (
    compute_corrections,
    frozen_days,
    station_stale,
)

from .conftest import fixture_json

TZ = ZoneInfo("America/New_York")
LAT, LON = 39.93999, -75.18101


def metric_stats():
    stats = fixture_json("station_statistics.json")
    result = {}
    for key in ("temperature", "dew_point", "wind_speed", "wind_gust", "irradiance"):

        def convert(v):
            if v is None:
                return None
            if key in ("temperature", "dew_point"):
                return (v - 32) * 5 / 9
            return v * 1.609344 if key.startswith("wind") else v

        result[key] = [
            {**r, **{s: convert(r[s]) for s in ("mean", "min", "max")}}
            for r in stats[f"sensor.league_{key}"]
        ]
    return result


def test_frozen_guard_regression():
    stats = fixture_json("station_statistics.json")
    assert (
        sorted(map(str, frozen_days(stats["sensor.league_temperature"], TZ)))
        == fixture_json("golden_corrections.json")["frozen_days"]
    )


def test_golden_corrections():
    members = parse_members(
        fixture_json("openmeteo_previous_runs.json"), DEFAULT_MODELS, previous=True
    )
    computed = compute_corrections(
        members, metric_stats(), date(2026, 10, 15), 60, TZ, LAT, LON
    )
    for model, variables in fixture_json("golden_corrections.json")["models"].items():
        for var, expected in variables.items():
            value = computed[model][var]
            assert value["n"] == expected["n"]
            assert value["value"] == pytest.approx(
                expected["value"] if expected["value"] is not None else 1, abs=0.002
            )
    assert computed["ecmwf_ifs025"]["wind_gusts_10m"]["insufficient_data"]


def test_insufficient_missing_and_clamp():
    t = datetime(2026, 10, 10, tzinfo=UTC)
    rows = {
        t + timedelta(hours=i): {"temperature_2m": 10, "wind_speed_10m": 1}
        for i in range(96)
    }
    stats = {"wind_speed": [dict(start=x.timestamp(), mean=10) for x in rows]}
    result = compute_corrections(
        {"model": rows}, stats, date(2026, 10, 15), 60, TZ, LAT, LON
    )["model"]
    assert result["wind_speed_10m"]["value"] == 2
    assert result["temperature_2m"] == dict(
        kind="offset", value=0, n=0, insufficient_data=True
    )
    stats["wind_speed"] = stats["wind_speed"][:71]
    assert (
        compute_corrections(
            {"model": rows}, stats, date(2026, 10, 15), 60, TZ, LAT, LON
        )["model"]["wind_speed_10m"]["value"]
        == 1
    )


def test_guard_requires_consecutive_and_recent():
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    rows = [
        dict(start=(now - timedelta(hours=h)).timestamp(), min=10, max=10)
        for h in (3, 2, 1)
    ]
    assert station_stale(rows, now)
    assert not station_stale(rows, now + timedelta(hours=1))
    rows[1]["start"] -= 36000
    assert not frozen_days(rows, TZ)


def test_preceding_solar_midpoint_and_gust_max():
    """An API value at 17:00 is paired with the 16:00 radiation bucket."""
    t = datetime(2026, 10, 1, 16, tzinfo=UTC)
    members = {
        "a": {
            t + timedelta(days=i): {
                "temperature_2m": 20,
                "wind_gusts_10m": 10,
                "shortwave_radiation": 100,
            }
            for i in range(80)
        }
    }
    stats = {
        "wind_gust": [dict(start=x.timestamp(), mean=1, max=20) for x in members["a"]],
        "irradiance": [dict(start=x.timestamp(), mean=50) for x in members["a"]],
    }
    # Larger window permits all 80 midday samples; use a latitude with midday sunlight.
    result = compute_corrections(
        members, stats, date(2026, 12, 21), 180, ZoneInfo("UTC"), 0, -60
    )["a"]
    assert result["wind_gusts_10m"]["value"] == 2
    assert result["shortwave_radiation"]["value"] == 0.5

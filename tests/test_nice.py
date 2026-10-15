import pytest

from custom_components.better_weather.nice import nice_score, score_components

from .conftest import fixture_json


@pytest.mark.parametrize("c", fixture_json("golden_nice_scores.json")["cases"])
def test_golden_scores(c):
    assert (
        nice_score(
            c["feels_like"],
            c["dew_point"],
            c["wind_speed"],
            c["wind_gust"],
            c["irradiance"],
            c["precipitation_intensity"],
            c["sun_elevation"],
        )
        == c["expected_score"]
    )


def test_rain_and_night():
    assert nice_score(65, 40, 2, 5, 800, 0.01, 45) == 10
    score, attrs = score_components(60, 40, 2, 5, 0, 0, 0)
    assert score == 100
    assert attrs["s_sun"] is None
    assert nice_score(60, 40, 2, 5, 1000, 0, 0) == score


def test_forecast_trace_precip_is_not_rain():
    """A 0.1 mm member-mean trace must not cap a forecast hour; >= 0.25 mm must."""
    from datetime import UTC, datetime

    from custom_components.better_weather.nice import forecast_score

    row = dict(
        time=datetime(2026, 5, 12, 16, tzinfo=UTC),
        apparent_temperature=21,
        dew_point_2m=3,
        wind_speed_10m=6,
        wind_gusts_10m=15,
        score_irradiance=700,
    )
    assert forecast_score(dict(row, precipitation=0.0), 39.94, -75.18)[0] == 100
    assert forecast_score(dict(row, precipitation=0.1), 39.94, -75.18)[0] == 100
    assert forecast_score(dict(row, precipitation=0.3), 39.94, -75.18)[0] == 10

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


def test_breakdown_and_why():
    score, attrs = score_components(57, 40, 4, 7, 300, 0, 19)
    rows = {r["factor"]: r for r in attrs["breakdown"]}
    assert set(rows) == {"temp", "dew", "wind", "sun"}
    assert rows["dew"]["lost"] == 0 and rows["wind"]["lost"] == 0
    assert sum(r["points"] for r in rows.values()) == pytest.approx(score, abs=0.6)
    assert attrs["why"].startswith(attrs["breakdown"][0]["label"])
    _, calm = score_components(66, 40, 2, 5, 0, 0, 0)
    assert calm["why"] == "Nothing holding it back"
    _, wet = score_components(66, 40, 2, 5, 800, 0.05, 45)
    assert wet["why"].startswith("Raining")


def test_clear_sky_curve_changes_only_sun():
    from custom_components.better_weather.nice import clear_sky_fraction

    curve = [[12.5, 0.4], [32.5, 0.65], [52.5, 0.75]]
    cfg = {"clear_sky_curve": curve}
    assert clear_sky_fraction({"clear_sky_frac": 0.75, **cfg}, 5) == 0.4
    assert clear_sky_fraction({"clear_sky_frac": 0.75, **cfg}, 22.5) == pytest.approx(0.525)
    assert clear_sky_fraction({"clear_sky_frac": 0.75, **cfg}, 70) == 0.75
    # Low sun, clear sky reading 45% of theory: flat 0.75 punishes it, the curve doesn't.
    import math

    sz = math.sin(math.radians(15))
    irr = 0.45 * 1098 * sz * math.exp(-0.057 / sz)
    flat = score_components(66, 40, 2, 5, irr, 0, 15)[1]
    learned = score_components(66, 40, 2, 5, irr, 0, 15, cfg)[1]
    assert flat["s_sun"] < 0.7 and learned["s_sun"] == 1
    assert flat["s_temp"] == learned["s_temp"] and flat["s_dew"] == learned["s_dew"]


def test_learn_clear_sky_curve_from_real_station():
    from zoneinfo import ZoneInfo

    from custom_components.better_weather.correction import learn_clear_sky_curve

    stats = fixture_json("station_statistics.json")["sensor.league_irradiance"]
    curve = learn_clear_sky_curve(stats, ZoneInfo("America/New_York"), 39.94, -75.18)
    assert len(curve) >= 3
    low = [f for e, f in curve if e < 20]
    high = [f for e, f in curve if e >= 30]
    # This station sees less of a clear sky at low sun than at midday (horizon/tilt).
    assert low and high and max(low) < min(high)
    assert learn_clear_sky_curve(stats[:20], ZoneInfo("America/New_York"), 39.94, -75.18) == []

from custom_components.better_weather.nice import nice_score, score_components
from .conftest import fixture_json


def test_golden_scores():
    for c in fixture_json("golden_nice_scores.json")["cases"]:
        assert nice_score(c["feels_like"], c["dew_point"], c["wind_speed"], c["wind_gust"],
                          c["irradiance"], c["precipitation_intensity"], c["sun_elevation"]) == c["expected_score"]


def test_rain_and_night():
    assert nice_score(65, 40, 2, 5, 800, 0.01, 45) == 10
    score, attrs = score_components(60, 40, 2, 5, 0, 0, 0)
    assert score == 100
    assert attrs["s_sun"] is None
    assert nice_score(60, 40, 2, 5, 1000, 0, 0) == score

import pytest

from custom_components.better_weather.station import metric_statistics, metric_value


def test_native_units_and_invalid_values():
    assert metric_value(68, "temperature", "°F") == 20
    assert metric_value(10, "wind_gust", "mph") == pytest.approx(16.09344)
    assert metric_value(29.92, "pressure", "inHg") == pytest.approx(1013.2, abs=0.1)
    assert metric_value(1, "precipitation_rate", "in/h") == 25.4
    assert metric_value("unavailable", "temperature", "°F") is None
    assert metric_value(10, "temperature", "nonsense") is None
    assert metric_value(10, "wind_speed", None) is None
    assert metric_value(10, "precipitation_rate", "nonsense") is None


def test_guard_uses_original_sensor_unit(hass):
    hass.states.async_set("sensor.t", 70, {"unit_of_measurement": "°F"})
    stats = metric_statistics(
        hass,
        {"station_temperature": "sensor.t"},
        {"sensor.t": [dict(start=0, mean=70, min=70, max=70.015)]},
    )
    assert stats["temperature"][0]["max"] - stats["temperature"][0]["min"] < 0.01
    assert not stats["temperature"][0]["flat"]


def test_unit_falls_back_to_statistics_metadata(hass):
    """No state yet (integration still loading) -> rows are in the stored statistics unit."""
    stats = metric_statistics(
        hass,
        {"station_temperature": "sensor.t"},
        {"sensor.t": [dict(start=0, mean=68, min=68, max=68.5)]},
        {"sensor.t": "°F"},
    )
    assert stats["temperature"][0]["mean"] == 20


def test_unitless_restored_state_uses_statistics_unit(hass):
    hass.states.async_set("sensor.t", "unavailable", {"restored": True})
    stats = metric_statistics(
        hass,
        {"station_temperature": "sensor.t"},
        {"sensor.t": [dict(start=0, mean=68, min=68, max=68.5)]},
        {"sensor.t": "°F"},
    )
    assert stats["temperature"][0]["mean"] == 20


def test_no_unit_anywhere_is_skipped(hass):
    assert metric_statistics(
        hass, {"station_temperature": "sensor.t"}, {"sensor.t": [dict(start=0, mean=1)]}
    ) == {}

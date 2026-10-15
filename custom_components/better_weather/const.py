"""Integration constants and the canonical comfort preferences."""
DOMAIN = "better_weather"
DEFAULT_MODELS = ["gem_seamless", "icon_seamless", "ecmwf_ifs025"]
MODELS = DEFAULT_MODELS + ["gfs_seamless", "ukmo_seamless", "meteofrance_seamless", "jma_seamless"]
DEFAULTS = dict(day_lo=62, day_hi=72, dusk_lo=52, dusk_hi=66, band_full_sun_elev=25,
                cold_falloff=12, hot_falloff=8, dew_ok=48, dew_bad=62, wind_ok=8, wind_bad=20,
                gust_ok=18, gust_bad=35, sun_min_elev=10, clear_sky_frac=0.75,
                w_temp=0.30, w_dew=0.30, w_sun=0.25, w_wind=0.15, rain_cap=10)
OPTION_DEFAULTS = {**DEFAULTS, "models": DEFAULT_MODELS, "correction_window_days": 60,
                   "correction_enabled": True, "nice_wake_hour": 8, "nice_sleep_hour": 20,
                   "day_rain_cap": 35, "rain_hour_min_in": 0.01}
STATION_KEYS = ["temperature", "dew_point", "humidity", "wind_speed", "wind_gust", "wind_bearing",
                "pressure", "irradiance", "precipitation_rate", "uv_index", "feels_like"]
VARIABLES = ["temperature_2m", "apparent_temperature", "dew_point_2m", "relative_humidity_2m",
             "precipitation", "precipitation_probability", "weather_code", "cloud_cover", "wind_speed_10m",
             "wind_gusts_10m", "wind_direction_10m", "shortwave_radiation", "pressure_msl", "uv_index", "is_day"]
CORRECTION_VARS = {"temperature_2m": ("temperature", "offset"), "dew_point_2m": ("dew_point", "offset"),
                   "wind_speed_10m": ("wind_speed", "ratio"), "wind_gusts_10m": ("wind_gust", "ratio"),
                   "shortwave_radiation": ("irradiance", "ratio")}
PRECEDING = {"shortwave_radiation", "precipitation"}
GROUP_CODES = ((0,), (1, 2), (3,), (45, 48), (51, 53, 55, 56, 57),
               (61, 63, 65, 66, 67, 80, 81, 82), (71, 73, 75, 77, 85, 86), (95, 96, 99))
GROUP_CONDITIONS = ("sunny", "partlycloudy", "cloudy", "fog", "rainy", "rainy", "snowy", "lightning-rainy")
WMO_TO_CONDITION = {code: GROUP_CONDITIONS[i] for i, codes in enumerate(GROUP_CODES) for code in codes}
ATTRIBUTION = "Open-Meteo (GEM/ICON/ECMWF blend, bias-corrected to your station)"

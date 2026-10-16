# Better Weather

A Home Assistant custom integration that blends global Open-Meteo forecasts, learns
flat bias corrections from your own weather station, and scores outdoor comfort.
Built and tested against **Home Assistant 2026.10.1 / Python 3.14**.

GEM, ICON and ECMWF receive equal weight. Your station supplies current conditions;
the corrected blend supplies hourly and daily forecasts through Home Assistant's
standard weather services. No API key or additional Python packages are needed.

## Why this blend?

A walk-forward backtest against a Philadelphia station, November 2025–October 2026,
compared the mean absolute error of the outdoor comfort score:

| Forecast | Comfort score MAE |
|---|---:|
| Raw GFS (the model behind PirateWeather) | 6.67 |
| Raw GEM + ICON + ECMWF, equal weights | 5.55 |
| Same blend with trailing 60-day flat corrections | **4.87** |

These are results at one station, not a promise for every location. Ranking models
by recent skill did not give a reliable improvement, so v1 keeps equal weights.

## Install

1. In HACS, open **Custom repositories**, add
   `https://github.com/leboff/ha-better-weather`, and choose **Integration**.
2. Download **Better Weather** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Better Weather**.

For a manual installation, copy `custom_components/better_weather` into your
Home Assistant configuration's `custom_components` directory and restart.
The repository must be published before the HACS URL will work.

## Configure

Choose a name and coordinates (initially your Home Assistant location), then select
your station's temperature sensor. The remaining station sensors are optional:
dew point, humidity, wind speed, gust, bearing, pressure, irradiance, rain rate,
UV index and feels-like temperature. Sensors can be changed or cleared later with
**Reconfigure** on the integration menu.

Station temperatures may be °C or °F, wind units supported by Home Assistant are
converted to km/h, and pressure units are converted to hPa. Irradiance must be W/m²;
rain rate must be mm/h or in/h. Correction and grading inputs need recorder hourly
statistics, normally provided by sensors with `state_class: measurement`.
Enable recording for those sensors and keep their units consistent with their
statistics. Excluding a station sensor from recorder prevents learning from it.

**Configure** opens options: models, a 14–180 day correction window (default 60),
a correction toggle, comfort preferences, local wake/sleep hours (default 08–20),
and rain thresholds. Changing options reloads the entry once. Locations rounded to
four decimal places are unique, so multiple different locations are supported.

## Entities

Entity IDs below assume the default name; Home Assistant may add a suffix if an ID
already exists. All six entities belong to one service device.

| Entity | Purpose |
|---|---|
| `weather.better_weather` | Station current conditions; up to 168 hourly forecasts and 8 local daily forecasts |
| `sensor.better_weather_nice_right_now` | Current outdoor comfort, 0–100; station first, forecast fallback |
| `sensor.better_weather_nice_today` | Observed elapsed hours plus corrected forecast hours during the waking day |
| `sensor.better_weather_nice_tomorrow` | Tomorrow's forecast comfort; suitable for a “nice day tomorrow” automation |
| `sensor.better_weather_corrections` | Correction status, per-model offsets/ratios, sample counts and frozen days |
| `sensor.better_weather_forecast_skill` | Corrected comfort-score MAE over the last 30 local dates |

Weather values use Home Assistant's display units. Comfort thresholds always use
°F and mph, matching the canonical comfort formula; irradiance stays in W/m².
Low-sun comfort favors cooler temperatures and omits the sunshine weight below
10° solar elevation. Any rain caps an hourly comfort score at 10. A rainy waking
hour also caps the day score at 35 by default. Day attributes show each hour,
its score and source, plus rain-hour counts. Tomorrow also exposes temperature
high/low, average dew point and mean model temperature spread in °F.

Live station comfort requires temperature or feels-like, dew point, wind, gust,
irradiance and rain rate. Missing inputs use the current blended forecast; if
that is also incomplete, the comfort entity is unavailable. An unconfigured
feels-like sensor uses station temperature. Weather current conditions independently
fall back for each missing station value.

The skill entity starts unavailable until at least one saved forecast has a full
observed waking day. Attributes include raw-score MAE, high-temperature MAE in °F,
rain prediction accuracy, precision/recall at comfort ≥85, and the last ten graded
days. Rates are fractions from 0 to 1. Records retain up to 400 target dates.
Snapshots are taken at local 20:00 for tomorrow; grading happens after sleep hour
once recorder has published all waking-hour statistics. Startup catches up missed
grading and an evening snapshot. Historical forecasts that were never captured
are not reconstructed. Frozen station days are excluded.

## Move from another weather integration

Add Better Weather alongside your existing provider. Compare a few days, then
select `weather.better_weather` in weather cards and replace old entity references
in automations. Standard `weather.get_forecasts` hourly/daily service calls work
with this entity. After updating references, disable or remove the old integration
if desired. This integration does not rename or migrate existing entities.

For a “nice day tomorrow” automation, use the tomorrow sensor with a numeric-state
condition above your preferred threshold (85 is the skill log's nice-day cutoff).

## How learning works

Every 30 minutes, Open-Meteo supplies eight days of forecasts. Once a day and on
setup, previous-day model runs are paired with your station's hourly recorder
statistics for the trailing local-date window:

- Temperature and dew point: subtract the mean model-minus-station offset.
- Wind: multiply by the station/model ratio; gusts use the station hourly maximum.
- Irradiance: learn a ratio using hours with solar elevation at least 20°.

Each model/variable needs 72 paired samples. Ratios stay between 0.2 and 2.0;
insufficient variables use neutral corrections. A variable a model never publishes
(ECMWF has no previous-run gusts) is marked `unsupported: true` and stays neutral; it
doesn't count against the diagnostic state, which is `ok` when every correction the
models can provide has enough data and `insufficient_data` only for a real station gap.

A run of three consecutive hourly temperature buckets with less than 0.01 variation
inside each bucket excludes every local date it touches. Three flat recent completed
hours also mark the station stale, making live weather and comfort use the blend.
The live guard is checked every five minutes. Missing statistics cannot prove a
freeze. Rain and radiation from Open-Meteo describe the preceding hour; values
are shifted to the start of the hour they describe before blending or scoring.

Corrected members are averaged per available variable. Wind bearing uses a circular
mean; conditions use WMO group votes with more severe ties. Relative humidity is
recomputed from corrected temperature and dew point. Rain, weather codes, cloud
cover, pressure, UV and precipitation probability are uncorrected. Without a learned
irradiance ratio, comfort scoring uses 75% of model radiation as the station scale.

## Limitations

- Global models cannot resolve every local storm, valley or station exposure.
- Model horizons and variable availability differ. Missing fields are omitted;
  incomplete comfort hours are excluded and visible through hour counts.
- A partial observed day is never assigned a skill grade. Today can show a partial
  score when observations are missing; elapsed hours are not replaced by hindsight
  forecasts.
- Comfort preferences are subjective. The solar calculation is a small NOAA
  approximation, not an astronomical ephemeris.
- Recorder units must match the sensor's reported unit. A unit change within the
  training window can require clearing/rebuilding the affected recorder statistics.
- Corrections are refreshed on setup rather than persisted. Transient failures
  keep last-good in-memory corrections/forecasts; a fresh startup with a failed
  correction fetch uses neutral corrections.
- The requested HACS metadata lists 2025.6.0, but this release is validated only
  against HA 2026.10.1 and uses Python 3.14 syntax. Use the tested HA version.

## Development

Real fixtures in `tests/fixtures` are read-only. Their reference generator and
canonical comfort template are in `tools/`. See [docs/SPEC.md](docs/SPEC.md) for the
build contract and [NOTES_FOR_REVIEW.md](NOTES_FOR_REVIEW.md) for implementation
decisions and validation.

```sh
/root/ha-wled-alerts-venv/bin/python -m pytest -q
/root/ha-wled-alerts-venv/bin/python -m compileall -q custom_components
```

Tests use `pytest-homeassistant-custom-component==0.13.372`, HA 2026.10.1 and Python
3.14. HTTP is mocked with real Open-Meteo responses, recorder statistics use the
real station fixture, and golden calculations are checked directly.

Weather data: [Open-Meteo](https://open-meteo.com/), provided under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Respect the service's
usage terms. Code is MIT licensed.

### Daily forecast sensor (v0.2.0)

`sensor.<name>_daily_forecast`: state is today's condition, and the `forecast` attribute is the daily
forecast in your unit system (same keys as `weather.get_forecasts`: `datetime`, `condition`,
`temperature`, `templow`, `precipitation`, `precipitation_probability`, `wind_speed`,
`wind_gust_speed`, `uv_index`). Dashboard cards can read it directly from state attributes without
calling a service, which makes it a drop-in replacement for the common "template forecast sensor" pattern.

### Correction recovery (v0.2.1)

- A failed correction refresh (Open-Meteo timeout/5xx, recorder error) or a run that pairs **zero**
  station samples keeps the last good corrections and retries every 15 minutes instead of waiting a day.
  The `corrections` sensor shows `error` until a retry succeeds.
- The fallback warning now names the exception type (`TimeoutError`, `ClientResponseError`, ...) so a
  timeout no longer logs as an empty message.
- Station history is converted using the recorder's stored statistics unit when the station sensor hasn't
  loaded yet (common right after a Home Assistant restart), so an early refresh no longer reads as "no data".

### Honest correction status (v0.2.2)

Variables a model doesn't publish at all (ECMWF previous-run gusts) are flagged `unsupported` in the
correction attributes and no longer hold the `corrections` sensor at `insufficient_data`. The state is now
`ok` when everything that can be corrected is corrected.

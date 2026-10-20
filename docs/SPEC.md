# ha-better-weather — Build Spec (v0.1.0)

A Home Assistant custom integration (HACS) that replaces a single-provider forecast
(PirateWeather) with an **equal-weight blend of three global NWP models from Open-Meteo**,
**bias-corrected against the user's own weather station** using HA's recorder long-term
statistics, plus a **"nice day" comfort score** (now / today / tomorrow) and a **self-grading
forecast-skill log**.

Repo: `leboff/ha-better-weather` · domain: `better_weather` · target HA 2026.10.1 (Python 3.14).
Author/commits: `Bryan Leboff <bleboff@gmail.com>`. License MIT.

---

## 0. Why (evidence — do not re-litigate, just build it)

Backtest Nov 2025–Oct 2026 against the League station (Philadelphia), day-ahead forecasts from
the Open-Meteo previous-runs API, walk-forward (corrections learned only from the past):

| setup | nice-score MAE |
|---|---|
| GFS raw (what PirateWeather is built on) | 6.67 |
| GEM+ICON+ECMWF equal blend, raw | 5.55 |
| **same blend, trailing-60-day flat bias correction** | **4.87** |

Findings that constrain the design (all with bootstrap CIs):
- **Equal weights.** Per-model ranking was noise (GEM best in one half, worst in the other). Do NOT
  implement skill-weighted blending in v1.
- **Flat offset per variable** is as good as per-hour (Δ CI −0.21…+0.05). Do NOT do per-hour tables.
- Window 30/60/90 days all equivalent → default 60, configurable.
- Adding GFS as a 4th member did not help. Default models = `gem_seamless, icon_seamless, ecmwf_ifs025`.
- The station **froze** for 170 h once (reported identical temp). Learning from a frozen station
  poisons corrections → a **frozen-station guard is mandatory**.

---

## 1. Architecture

```
custom_components/better_weather/
  __init__.py          setup/unload, coordinators, update listener
  manifest.json        domain better_weather, iot_class cloud_polling, config_flow true,
                       integration_type "service", dependencies ["recorder"], requirements [] (aiohttp is core)
  const.py             defaults, model list, variable map, WMO→condition map
  api.py               OpenMeteoClient (forecast + previous-runs), pure parsing helpers
  blend.py             PURE functions: blend members, apply corrections, aggregate daily
  correction.py        PURE: compute corrections from (prev-run forecasts, station stats); frozen guard
  nice.py              PURE: nice_score(...) — Python port of the canonical Jinja (see §5)
  coordinator.py       ForecastCoordinator (30 min), CorrectionCoordinator (daily + on setup; 15-min retry after a failed or empty run)
  weather.py           WeatherEntity (current + hourly + daily)
  sensor.py            nice_right_now, nice_today, nice_tomorrow, forecast_skill, correction diagnostics
  skill.py             forecast-skill log (Store) + scoring
  config_flow.py       config flow + options flow
  diagnostics.py       config-entry diagnostics (corrections, last fetch, skill summary)
  strings.json, translations/en.json, brand/icon.png (256²) + brand/icon@2x.png (512²)
tests/ ...             pytest-homeassistant-custom-component
hacs.json, README.md, LICENSE, setup.cfg, .github/workflows/validate.yml
```

**Keep the math in pure modules** (`blend.py`, `correction.py`, `nice.py`) with no `hass` import so
they are unit-tested directly against fixtures.

---

## 2. Config entry (config flow, single step `user`)

Fields (use `homeassistant.helpers.selector`; defaults pre-filled):
- `name` (text, default "Better Weather")
- `latitude`, `longitude` (number; default `hass.config.latitude/longitude`)
- Station sensors (EntitySelector, domain `sensor`; all optional except temperature):
  `station_temperature`, `station_dew_point`, `station_humidity`, `station_wind_speed`,
  `station_wind_gust`, `station_wind_bearing`, `station_pressure`, `station_irradiance`,
  `station_precipitation_rate`, `station_uv_index`, `station_feels_like`.
  **Auto-suggest**: if entities matching `sensor.*_temperature` etc. exist, nothing fancy needed — just defaults empty.
- unique_id = f"{round(lat,4)}_{round(lon,4)}"; abort `already_configured`.
- Validate by making one forecast request; on failure show `cannot_connect`.

Options flow (plain `OptionsFlow` + update listener → reload; NOT `OptionsFlowWithReload` combined with a listener):
- `models` (multi-select from: gem_seamless, icon_seamless, ecmwf_ifs025, gfs_seamless, ukmo_seamless, meteofrance_seamless, jma_seamless; default first three; min 1)
- `correction_window_days` (int 14–180, default 60)
- `correction_enabled` (bool, default true)
- Nice-score thresholds (all from §5 `DEFAULTS`, each a NumberSelector) — keep them in one dict in const.py
- `nice_wake_hour` (default 8), `nice_sleep_hour` (default 20)
- `day_rain_cap` (default 35), `rain_hour_min_in` (default 0.01)

All station sensors may be changed later via a **reconfigure** step (`async_step_reconfigure`).

---

## 3. Data sources

### 3.1 Forecast (every 30 min, `ForecastCoordinator`)
`GET https://api.open-meteo.com/v1/forecast`
params: `latitude, longitude, models=<comma list>, forecast_days=8, timezone=UTC, wind_speed_unit=kmh,
hourly=temperature_2m,apparent_temperature,dew_point_2m,relative_humidity_2m,precipitation,
precipitation_probability,weather_code,cloud_cover,wind_speed_10m,wind_gusts_10m,wind_direction_10m,
shortwave_radiation,pressure_msl,uv_index,is_day`

Multi-model responses suffix each key with `_<model>` (e.g. `temperature_2m_gem_seamless`). Any
value may be `null` (models have different horizons — ECMWF/GEM shorter on some vars). Fixture:
`tests/fixtures/openmeteo_forecast.json` (real response, Oct 15 2026).

Use `async_get_clientsession(hass)`; timeout 30 s; raise `UpdateFailed` on error; keep last good data.

### 3.2 Previous runs (daily, `CorrectionCoordinator`)
`GET https://previous-runs-api.open-meteo.com/v1/forecast`
params: same lat/lon/models, `timezone=UTC`, `wind_speed_unit=kmh`,
`hourly=temperature_2m_previous_day1,dew_point_2m_previous_day1,wind_speed_10m_previous_day1,
wind_gusts_10m_previous_day1,shortwave_radiation_previous_day1`,
`start_date=<today_local - window - 1>`, `end_date=<yesterday_local>`.
`*_previous_day1` = what the model predicted for that hour ~24 h earlier. Fixture:
`tests/fixtures/openmeteo_previous_runs.json`.

### 3.3 Station history (daily) — HA recorder long-term statistics
```python
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
stats = await get_instance(hass).async_add_executor_job(
    statistics_during_period, hass, start_utc, end_utc, set(statistic_ids), "hour", None, {"mean", "min", "max"})
```
Returns `{statistic_id: [{"start": float_ts, "end": float_ts, "mean", "min", "max"}, ...]}`
(start/end are **epoch seconds floats** in 2026.10). Values are in the **sensor's unit** — read
`hass.states.get(id).attributes["unit_of_measurement"]` and convert to metric with
`homeassistant.util.unit_conversion` (`TemperatureConverter`, `SpeedConverter`). Irradiance is W/m² already.
Fixture: `tests/fixtures/station_statistics.json` (°F, mph, W/m², in/h — the real League data).

---

## 4. Algorithms

### 4.1 Frozen-station guard (correction.py)
A local day is **frozen** if the station temperature series contains a run of **≥3 consecutive
hourly buckets with `abs(max - min) < 0.01`** touching that day (every day the run touches is excluded).
Excluded days contribute nothing to corrections or skill scoring. Expose
`frozen_days_in_window` in diagnostics + correction sensor attributes. If the station temperature has
been frozen for ≥3 h **right now** (live check on the last 3 hourly buckets), set a `station_stale`
flag: current conditions fall back to the blend instead of the station (see 4.4).

### 4.2 Corrections (correction.py) — per model, per variable, flat
Window: local dates `[today - window_days, today)`, frozen days excluded. Pair each previous-run
hour `t` (UTC, hour **start** per Open-Meteo hourly convention) with the station bucket whose
`start == t`.

| variable | station stat | kind | formula |
|---|---|---|---|
| temperature_2m | temperature `mean` (→ °C) | offset | `mean(model - station)` |
| dew_point_2m | dew_point `mean` (→ °C) | offset | `mean(model - station)` |
| wind_speed_10m | wind_speed `mean` (→ km/h) | ratio | `sum(station)/sum(model)` |
| wind_gusts_10m | wind_gust **`max`** (→ km/h) | ratio | `sum(station)/sum(model)` |
| shortwave_radiation | irradiance `mean` | ratio | `sum(station)/sum(model)` |

**Open-Meteo time convention (important):** instantaneous vars (temperature, dew point, wind, gusts)
are valid at timestamp `t`; **`shortwave_radiation` and `precipitation` are means/sums over the
PRECEDING hour** `[t-1h, t)`. So pair those with the station bucket whose `start == t - 1h`. Shortwave:
only use hours where solar elevation at the station-bucket midpoint (`bucket_start + 30 min`) is ≥ 20°.
Same convention applies when scoring forecast hours (§4.6) and when building the hourly forecast
(present the value at the hour it actually describes — document your choice in NOTES_FOR_REVIEW).

All golden corrections must match to **±0.002**. `ecmwf_ifs025` has **no gust data** in previous-runs
(all null) → its gust correction must come out `n=0` → neutral (ratio 1, `insufficient_data`, `unsupported: true`;
unsupported variables don't affect the sensor state).
Reference generator: `tools/make_fixtures.py` (+ `tools/solar.py`, `tools/live_score.j2`) — read it if
anything in the golden file is unclear; it is the source of truth for the fixtures.

Minimum samples: a correction is only applied if `n >= 72` (3 days of hours); otherwise
`offset = 0` / `ratio = 1` and attribute `insufficient_data: true`. Ratios are clamped to [0.2, 2.0].
Station sensors not configured → that variable is uncorrected.

Apply: `corrected = raw - offset` (offset vars) or `raw * ratio` (ratio vars). `apparent_temperature`
gets the **temperature_2m offset** subtracted. Relative humidity is **recomputed** from corrected
temperature + corrected dew point (Magnus formula). Precipitation, weather_code, cloud cover,
pressure, uv, probability are **not** corrected.

Golden values: `tests/fixtures/golden_corrections.json` (as_of local date 2026-10-15, window 60,
metric units, plus the list of frozen days the guard must find: 2026-08-20, 2026-09-29…2026-10-06).

Solar elevation: implement in a tiny pure function (NOAA approx — `tools/solar.py` is the verified
reference implementation; port it, parameterised by lat/lon). Do NOT add the `astral` dependency path for this.

### 4.3 Blend (blend.py)
For each hour and each variable: **arithmetic mean of available (non-null) corrected members**.
- `weather_code`: not averaged. Take the **most severe** code among members using WMO groups, but
  only if at least 2 members agree on its group (precip vs no precip); else the median-severity member.
  Simpler acceptable v1: majority vote on the WMO group (clear/partly/cloudy/fog/drizzle/rain/snow/thunder),
  tie → more severe. Map to HA conditions (const.py map; `is_day==0` + clear → `clear-night`).
- `precipitation_probability`: mean of available members (some models don't provide it → may be all null → omit).
- `wind_direction_10m`: circular mean (vector average).
- Hour included only if ≥1 member present for temperature.
Attributes on the weather entity: `models`, `member_count` (for the current hour), `model_spread_temp`
(max−min of corrected member temps for the current hour, °C→display unit handled by HA).

### 4.4 WeatherEntity (weather.py) — must be a drop-in for PirateWeather
- `supported_features = FORECAST_HOURLY | FORECAST_DAILY` (implement `async_forecast_hourly` / `async_forecast_daily`).
- Native units metric: `native_temperature_unit=°C`, `native_wind_speed_unit=km/h`,
  `native_pressure_unit=hPa`, `native_precipitation_unit=mm`. HA converts to the user's imperial system.
- **Current conditions = station when available and not stale** (temperature, dew point, humidity,
  wind speed, gust, bearing, pressure, uv, apparent temp from feels_like sensor if set); convert
  station units → native metric. Missing/unavailable station values fall back to the blend's current hour.
  `condition` always from the blend (current hour) — except if station precipitation rate > 0 → `rainy`
  (or `snowy` if corrected temp ≤ 0 °C).
- Hourly forecast: next 168 h from current hour: datetime (ISO UTC), condition, native_temperature,
  native_apparent_temperature, native_dew_point, humidity, native_precipitation, precipitation_probability,
  native_wind_speed, native_wind_gust_speed, wind_bearing, cloud_coverage, native_pressure, uv_index, is_daytime.
- Daily forecast: 7–8 days grouped by **local** date (`dt_util.as_local`): high/low temp (`native_temperature`,
  `native_templow`), sum precipitation, max precipitation_probability, mean wind, max gust, daytime-dominant
  condition (most severe hour-group between 08–20 local by count, tie → severe), uv max.
- After each coordinator update call `async_update_listeners(("daily","hourly"))` (the coordinator
  base does this via `_handle_coordinator_update` → `self.async_update_listeners`). Verify
  `weather.get_forecasts` service returns data in tests.
- `attribution = "Open-Meteo (GEM/ICON/ECMWF blend, bias-corrected to your station)"`.

### 4.5 Nice score (nice.py) — exact port, golden-tested
Canonical source: `tools/live_score.j2` (Jinja; read it). Port **exactly**:

```
DEFAULTS = dict(day_lo=62, day_hi=72, dusk_lo=52, dusk_hi=66, band_full_sun_elev=25,
                cold_falloff=12, hot_falloff=8, dew_ok=48, dew_bad=62, wind_ok=8, wind_bad=20,
                gust_ok=18, gust_bad=35, sun_min_elev=10, clear_sky_frac=0.75,
                w_temp=0.30, w_dew=0.30, w_sun=0.25, w_wind=0.15, rain_cap=10)
```
Units of the formula are **imperial** (°F, mph, W/m²) — convert metric inputs before scoring.
```
nice_score(feels_f, dew_f, wind_mph, gust_mph, irr_wm2, rain_rate, sun_elev_deg, cfg) -> int
  f  = clamp(sun_elev / band_full_sun_elev, 0, 1)
  lo = dusk_lo + (day_lo - dusk_lo) * f ; hi = dusk_hi + (day_hi - dusk_hi) * f
  s_temp = 1 if lo<=feels<=hi else max(0, 1-(lo-feels)/cold_falloff) if feels<lo else max(0, 1-(feels-hi)/hot_falloff)
  s_dew  = clamp(1 - (dew - dew_ok)/(dew_bad - dew_ok), 0, 1)
  s_wind = clamp(min(1, 1-(wind-wind_ok)/(wind_bad-wind_ok), 1-(gust-gust_ok)/(gust_bad-gust_ok)), 0, 1)
  num = w_temp*s_temp + w_dew*s_dew + w_wind*s_wind ; den = w_temp + w_dew + w_wind
  if sun_elev > sun_min_elev:
      sz = sin(radians(sun_elev)); clear = 1098*sz*exp(-0.057/sz)
      s_sun = min(1, irr / (clear*clear_sky_frac)); num += w_sun*s_sun; den += w_sun
  score = 100*num/den
  if rain_rate > 0: score = min(score, rain_cap)
  return int(round(score))      # Python round == Jinja round(0) (half-to-even) — keep it
```
Must match **every** case in `tests/fixtures/golden_nice_scores.json` exactly (400 cases).

### 4.6 Nice sensors (sensor.py)
- `sensor.<name>_nice_right_now` — live, from **station** values (feels_like or temperature, dew point,
  wind, gust, irradiance, rain rate) + `sun.sun` elevation computed by our solar function (don't depend
  on `sun.sun`). Recompute on any station-sensor state change (`async_track_state_change_event`) and
  every 5 min. `state_class: measurement`, unit none, icon `mdi:weather-sunny`. If the station is stale
  or a required input is unavailable → fall back to blend current hour; if that's missing → unavailable.
  Attributes: `s_temp, s_dew, s_wind, s_sun` (0..1, 2dp), `source` (`station`|`forecast`), `why` (plain-English
  factor losses), `breakdown` (per-factor points/max/lost/detail). v0.3.0: the sun factor's clear-sky fraction comes from
  a curve learned per station (see README → v0.3.0); the golden cases still use the flat `clear_sky_frac`.
- `sensor.<name>_nice_today` — blended **day score**: for local hours `wake..sleep-1` of today use the
  station's hourly score for hours already elapsed (from recorder hourly stats / an in-memory per-hour
  running mean of the right-now score — simplest: keep an in-memory dict hour→list of right-now samples,
  persisted via `Store`) and the corrected forecast hourly score for future hours; day score = mean of
  hourly scores; if any hour (observed: station rain rate mean ≥ `rain_hour_min_in` in/h; forecast:
  precipitation ≥ 0.25 mm in that hour) is rainy → each such hour scored min(score,10) and day capped at
  `day_rain_cap`. Attributes: `observed_hours`, `forecast_hours`, `hourly` (list of {hour, score, source}),
  `rain_hours`.
- `sensor.<name>_nice_tomorrow` — same day-score over tomorrow's wake..sleep hours, forecast only.
  Attributes: `hourly`, `rain_hours`, `high_f`, `low_f`, `dew_point_avg_f`, `model_spread_f`.
  This is the sensor the "nice day tomorrow" automation will use.
- Forecast hourly score: feels = corrected apparent_temperature, dew, wind = corrected wind_speed, gust =
  corrected gust, irr = corrected shortwave_radiation (the irradiance correction ratio already maps model
  shortwave to *station-scale*, which is what the score's clear_sky_frac=0.75 expects; if the irradiance
  correction is unavailable, multiply by 0.75), sun elevation at hour **midpoint**.

### 4.7 Correction diagnostic sensor
`sensor.<name>_corrections` — state = `ok` | `insufficient_data` (a published variable lacks samples) | `disabled` | `error`; attributes:
per-model dict of {variable: {kind, value, n}}, `window_days`, `frozen_days_in_window`, `computed_at`.
`entity_category: diagnostic`.

### 4.8 Forecast skill (skill.py) — self-grading
- Every day at local 20:00 (and on startup if missed), snapshot **tomorrow's** forecast day-score + the
  corrected daily high/low + predicted rain(bool) and also the **raw (uncorrected) blend** day-score into a
  `Store` (`.storage/better_weather.<entry_id>.skill`), keyed by target date.
- Every day after local `sleep_hour`, finalize **today**: compute the observed day score from station hourly
  stats (recorder, wake..sleep, frozen days → skipped), observed high, observed rain hours, and store.
- `sensor.<name>_forecast_skill` — state = **30-day MAE of the corrected day-score** (1 dp);
  attributes: `raw_mae_30d`, `n_days`, `high_temp_mae_f`, `rain_hit_rate`, `nice_day_precision`,
  `nice_day_recall` (threshold 85), `last_10` (list of {date, forecast, raw_forecast, observed}).
  `entity_category: diagnostic`. Keep max 400 days in the store.

---

## 5. Engineering rules

- Read HA 2026.10.1 source in `/root/ha-wled-alerts-venv/lib/python3.14/site-packages/homeassistant/`
  instead of guessing APIs (WeatherEntity: `components/weather/__init__.py`; a model integration:
  `components/open_meteo/`). Run Python from `/root` or the repo root, never from inside site-packages.
- HA 2026.10 uses `probatio` for schemas: `try: import probatio as vol except ImportError: import voluptuous as vol`.
- Every callback for `async_track_time_interval` / `async_track_time_change` / `async_call_later` must be `@callback`.
- Use `DataUpdateCoordinator` with `config_entry=entry`. Store runtime data on `entry.runtime_data` (typed dataclass).
- No blocking I/O in the event loop. Recorder calls through `get_instance(hass).async_add_executor_job`.
- Unique IDs: `f"{entry.entry_id}_{key}"`. One device per entry (`DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=title, manufacturer="Open-Meteo blend", entry_type=DeviceEntryType.SERVICE)`).
- `has_entity_name = True` + translation keys for entity names.
- Logging: debug for fetches; warning once when corrections fall back.
- No hardcoded Philly values outside tests/fixtures. Defaults come from `hass.config`.

## 6. Tests (pytest-homeassistant-custom-component 0.13.372, `asyncio_mode = auto`)

Venv: `/root/ha-wled-alerts-venv` (HA 2026.10.1, Python 3.14) — reuse it; run
`cd /root/ha-better-weather && /root/ha-wled-alerts-venv/bin/python -m pytest -q`.
Add `tests/conftest.py` with `auto_enable_custom_integrations` (autouse) like
`/root/ha-wled-alerts/tests/conftest.py`. HTTP via `aioclient_mock` returning the fixtures.

Required tests (minimum):
1. `test_nice.py` — all 400 golden cases exact; rain cap; sun below threshold drops sun weight.
2. `test_correction.py` — golden corrections from fixtures (all ±0.002, ecmwf gust neutral); frozen-day
   detection finds exactly the golden frozen days; n<72 → neutral + `insufficient_data`; ratio clamp;
   missing station sensor → uncorrected.
3. `test_blend.py` — mean of available members with nulls; circular wind mean (350° & 10° → 0°);
   weather_code group vote; RH recomputed from corrected T/Td; daily aggregation by local date across DST
   (use Nov 1 2026 fall-back day with a synthetic series).
4. `test_config_flow.py` — user flow creates entry; duplicate aborts; cannot_connect; options flow
   (models multi-select, window) reloads; reconfigure changes station sensors.
5. `test_weather.py` — set up entry with mocked HTTP + mocked statistics
   (patch `custom_components.better_weather.coordinator.statistics_during_period` or the wrapper you write),
   station states set with °F/mph units; assert current temp comes from station (converted correctly
   to the user's units), `weather.get_forecasts` hourly & daily non-empty, daily high ≥ low, condition valid;
   station temp frozen 3 h → falls back to blend; HTTP failure keeps last data and entity stays available
   for one failure.
6. `test_sensors.py` — nice_right_now matches `nice_score` on the set station states; nice_tomorrow
   computed from forecast with rain hour capping; corrections sensor attributes; forecast_skill after
   simulating a snapshot + finalize with `freezer`.
7. `test_init.py` — unload cleans up; reload doesn't leak listeners.

**Prove a regression test catches the bug** for the frozen guard: stash the guard, run that test, it must fail.

## 7. Publishing (do it at the end, after all tests pass)

- README.md: what it is, why (short version of §0 with the table), install via HACS custom repo,
  config, entities, migrating from another weather integration, how correction works, limitations.
- `hacs.json`: `{"name": "Better Weather", "render_readme": true, "homeassistant": "2025.6.0"}`.
- `.github/workflows/validate.yml`: copy `/root/ha-wled-alerts/.github/workflows/validate.yml`.
- brand icons: generate simple PNGs with Pillow if available in the venv (sun+cloud glyph, transparent bg),
  256² and 512².
- `translations/en.json` = copy of `strings.json`.
- Commit as `Bryan Leboff <bleboff@gmail.com>` with clear messages.
- **Do NOT create the GitHub repo or push** — the orchestrator (Alice) does that after review.

## 8. Definition of done

- `pytest -q` green, zero warnings about un-awaited coroutines or thread-safety.
- `python -m compileall custom_components` clean; `manifest.json` valid (`version` "0.1.0", `codeowners` ["@leboff"],
  `documentation`/`issue_tracker` → github.com/leboff/ha-better-weather).
- A `NOTES_FOR_REVIEW.md` at repo root listing: decisions made where this spec was ambiguous, anything
  skipped, and known limitations.

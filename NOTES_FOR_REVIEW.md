# Notes for review

Implemented sections 1–7 of `docs/SPEC.md`. Nothing requested was intentionally
skipped. No repository was created and nothing was pushed. Real fixtures and their
reference generator were left unchanged.

## Validation

Environment: `/root/ha-wled-alerts-venv`, Home Assistant 2026.10.1, Python 3.14,
pytest-homeassistant-custom-component 0.13.372.

- `/root/ha-wled-alerts-venv/bin/python -m pytest -q`: **447 passed**.
- All 400 golden comfort cases pass individually and exactly. All golden
  correction values match within ±0.002, including sample counts. ECMWF gusts are
  neutral with `n=0` and `insufficient_data=true`.
- Frozen guard regression proof: temporarily replaced `frozen_days`' return with
  an empty set, ran `tests/test_correction.py::test_frozen_guard_regression`, and
  confirmed failure (all nine expected frozen dates disappeared). Restored the
  guard before committing and reran the passing full suite.
- Actual `weather.get_forecasts` hourly and daily service calls return forecasts.
  Tests cover station/display unit conversion, station state events, frozen live
  fallback, failed HTTP retention, config/options/reconfigure flows, mixed-day
  scoring, persisted snapshots/finalization, scheduled 20:00 snapshots, startup
  catch-up, frozen grade invalidation, retention, unload and reload cleanup.
- Recorder calls use the real recorder executor in integration tests; a dedicated
  test asserts that the statistics function runs outside the event-loop thread.
  No un-awaited coroutine or thread-safety warnings occurred in the passing suite.
- `/root/ha-wled-alerts-venv/bin/python -m compileall -q custom_components`: clean.
- Ruff formatting and `E4,E7,E9,F,I` checks pass for integration/test code.
- Manifest and HACS JSON verified, English translation matches `strings.json`,
  transparent RGBA icons are 256²/512², and the validation workflow matches the
  sibling integration byte-for-byte. Icons were visually inspected.

## Decisions where the specification left room

- **Hour convention:** internal and published hourly datetimes are UTC **hour
  starts**. Radiation and precipitation at Open-Meteo timestamp `t` are moved to
  `t-1h`, while instantaneous variables stay at `t`. An hourly row at 12:00 thus
  combines conditions at 12:00 with radiation/rain for 12:00–13:00. Correction
  pairing and comfort scoring use this same normalized representation. Solar
  elevation for hourly radiation/comfort is evaluated at 12:30. Daily sums are
  grouped by the normalized hour's local date, including DST's 23/25-hour days.
- **Weather APIs:** use HA 2026.10.1's `SingleCoordinatorWeatherEntity` and its
  `_async_forecast_hourly`/`_async_forecast_daily` callbacks. That base implements
  the public async forecast methods and updates forecast subscribers after
  coordinator notifications. It was verified through the real weather service.
- **Available members:** each variable averages its own available members.
  `member_count` counts available temperatures, and a row needs at least one
  temperature. Conditions vote on WMO groups, with severity breaking ties.
  Wind bearing uses the vector mean (first member if opposing vectors cancel).
- **Spread units:** HA converts standard weather fields but does not convert
  arbitrary attributes. The weather entity explicitly converts the temperature
  spread with HA's `TemperatureDeltaConverter` into its selected display unit,
  and includes `model_spread_temp_unit`. Tomorrow's `model_spread_f` is the mean
  hourly temperature spread over all available hours of that local day.
- **Station completeness:** weather values independently fall back to the current
  blend. Station comfort requires temperature/feels-like, dew point, wind, gust,
  irradiance and rain rate; an unavailable rain sensor cannot silently imply dry
  conditions. Missing feels-like uses temperature. Without complete comfort inputs
  it uses the forecast, or becomes unavailable if the forecast is incomplete.
  A station rain override uses corrected blend temperature to choose snowy/rainy.
- **Frozen detection:** consecutive means adjacent UTC bucket starts, with gaps
  breaking runs. Every local date touched by a qualifying run is excluded. The
  flatness comparison is preserved in the original sensor unit before conversion.
  Live stale status uses the last three completed hourly buckets, so an old flat
  run cannot mark today's station stale. It is refreshed every five minutes.
- **Observed day scores:** the permitted live-sample approach is persisted in a
  separate per-entry observations `Store`, retaining three days. An elapsed hour
  prefers the mean of live station scores, with recorder-derived scores as a
  backfill. Its rainy status uses the mean observed rate in in/h. Missing elapsed
  observations are omitted rather than filled with later forecasts; attributes
  reveal partial-day coverage. DST hours retain unique UTC keys.
- **Skill grading:** uses recorder statistics, never forecast fallback or live
  samples. Gust uses hourly max; feels-like falls back to temperature. All waking
  hours are required before assigning a grade, and missing recorder data is
  retried every five minutes and at startup. Adjacent hours are queried to catch
  frozen runs crossing midnight. Later frozen detection also invalidates an
  already graded date. Daily observed high is the maximum of the target date's
  available temperature maxima when finalized.
- **Skill timing/metrics:** tomorrow's first successful snapshot at/after local
  20:00 is immutable. Startup catches that evening's missed snapshot and pending
  past finalizations; it does not reconstruct forecasts for nights HA was offline.
  The exact 20:00 and sleep-hour callbacks supplement the five-minute timer.
  MAE uses the trailing 30 local dates inclusive of today. Rain hit rate means
  binary rain/no-rain accuracy; precision and recall use ≥85. Undefined rates
  and MAE with no graded days are `None` (skill state unavailable). `last_10`
  contains the most recent graded dates within that 30-day reporting window.
- **Correction diagnostics:** `insufficient_data` means at least one selected
  model/variable lacks sufficient samples; other usable corrections still apply.
  `frozen_days_in_window` is a sorted list of local dates, not just a count.
  Diagnostic payloads omit coordinates and station entity identifiers.
- **Failure handling:** a correction refresh failure keeps last-good or neutral
  corrections and reports `error` without preventing initial weather setup.
  Forecast request failures use `UpdateFailed` and retain the last good data;
  the weather entity remains available while it has that data. Corrections are
  computed on setup and every 24 hours; live recorder checks are separate.
- **Reconfiguration:** optional sensor suggestions are removable. Reconfigure
  updates data with `async_update_and_abort`, letting the existing entry listener
  reload exactly once. Options use plain `OptionsFlow` with that same listener.
  Positive falloffs/weights and ordered thresholds are validated to prevent
  undefined formula denominators; wake hour must precede sleep hour.

## Known limitations

- Global model resolution, varying variable horizons, and recorder gaps limit
  forecast/scoring coverage. No statistical grade is manufactured for a partial
  observed day. The frozen guard needs recorder statistics to detect a freeze.
- The earliest finalized daily high can omit late-evening temperatures after the
  configured sleep hour; score and rain grading cover the configured waking day.
- Correction history uses the current station unit as requested. Historical unit
  changes need care because old recorder statistics may use another unit.
- Comfort preference changes affect subsequent observed grading; existing stored
  snapshot scores are retained, so the rolling skill window can temporarily mix
  preference settings. Station sensor changes can similarly change the reference.
- Forecasts/corrections are retained in memory across request failures, not across
  HA restarts. Only observations and the skill log are persisted. With a failed
  correction fetch on fresh startup, forecasts use neutral corrections until the
  next successful correction refresh.
- HACS's `homeassistant: 2025.6.0` is copied exactly from the requested spec. The
  code targets HA 2026.10.1/Python 3.14 and has not been validated on older HA;
  the metadata minimum is not a compatibility claim. Consider raising it before
  publishing if older installations should be prevented from downloading.
- The copied workflow is present; GitHub-hosted HACS/hassfest jobs were not run
  locally or remotely. No live Open-Meteo requests were needed: integration tests
  use the supplied real responses and station data.

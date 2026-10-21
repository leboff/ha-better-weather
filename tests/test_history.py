"""Day ranking: every measured station day rescored with the current rules, latest day's rank."""

from datetime import date, datetime, timedelta

from custom_components.better_weather.history import history_summary, rank_of, scoring_signature


def test_rank_ties_share_the_better_rank():
    days = {"a": {"score": 90}, "b": {"score": 95}, "c": {"score": 90}, "d": {"score": 50}}
    assert rank_of(days, "b") == 1
    assert rank_of(days, "a") == rank_of(days, "c") == 2
    assert rank_of(days, "d") == 4


def test_summary_reports_latest_day():
    days = {
        "2026-10-18": {"score": 85.7, "rain": False},
        "2026-10-19": {"score": 90.1, "rain": False},
        "2026-10-17": {"score": 35.0, "rain": True},
    }
    n, attrs = history_summary(days)
    assert n == 3
    assert attrs["since"] == "2026-10-17"
    assert attrs["latest"] == dict(date="2026-10-19", score=90.1, rain=False, rank=1, tied=0, of=3)
    assert [d["date"] for d in attrs["top_10"]] == ["2026-10-19", "2026-10-18", "2026-10-17"]
    assert history_summary({}) == (None, {})


def test_signature_tracks_scoring_options_only():
    base = {"dew_ok": 48, "nice_wake_hour": 8, "models": ["a"]}
    assert scoring_signature(base) == scoring_signature(base | {"models": ["b"]})
    assert scoring_signature(base) != scoring_signature(base | {"dew_ok": 50})
    assert scoring_signature(base) != scoring_signature(base | {"nice_sleep_hour": 21})


async def test_rebuild_ranks_fixture_history_and_matches_grading(hass, setup_entry, statistics):
    runtime = setup_entry.runtime_data
    history, skill = runtime.history, runtime.skill
    assert history.stale
    await history.async_rebuild()
    assert not history.stale
    frozen = runtime.corrections.frozen
    days = history.days
    # Fixture: Aug 15 – Oct 14 2026 local. Frozen station days are never ranked; today isn't complete.
    assert days and min(days) >= "2026-08-15" and max(days) == "2026-10-14"
    assert not {d.isoformat() for d in frozen} & set(days)
    assert "2026-10-15" not in days
    # Same number the skill log grades the day with.
    target = date(2026, 10, 14)
    stats = await history._stats(target, target + timedelta(days=1))
    expected = skill.observed_day(skill.observed_hours(stats), target)
    assert days["2026-10-14"]["score"] == expected[0]
    # The sensor shows the latest measured day's rank among all of them.
    runtime.forecast.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.better_weather_nice_day_rank")
    latest = state.attributes["latest"]
    assert latest["date"] == "2026-10-14"
    assert int(state.state) == latest["rank"] == rank_of(days, "2026-10-14")
    assert latest["of"] == state.attributes["days_ranked"] == len(days)
    assert state.attributes["top_10"][0]["score"] == max(d["score"] for d in days.values())


async def test_rebuild_persists_and_option_change_marks_stale(hass, setup_entry, statistics):
    from custom_components.better_weather.history import DayHistory

    runtime = setup_entry.runtime_data
    await runtime.history.async_rebuild()
    reloaded = DayHistory(hass, setup_entry, runtime.skill)
    await reloaded.async_load()
    assert reloaded.days == runtime.history.days and not reloaded.stale
    runtime.skill.cfg = runtime.skill.cfg | {"dew_ok": 50}
    assert reloaded.stale


async def test_evening_finalize_adds_today_once_complete(hass, setup_entry, freezer, statistics):
    runtime = setup_entry.runtime_data
    history = runtime.history
    await history.async_rebuild()
    del history.days["2026-10-14"]
    freezer.move_to("2026-10-15T01:30:00Z")  # Oct 14, 21:30 local: after sleep hour
    await history.async_tick()
    assert "2026-10-14" in history.days


async def test_frozen_day_found_later_is_dropped(hass, setup_entry, statistics):
    runtime = setup_entry.runtime_data
    history = runtime.history
    await history.async_rebuild()
    victim = max(history.days)
    runtime.corrections.frozen.add(date.fromisoformat(victim))
    await history.async_tick()
    assert victim not in history.days


async def test_scheduled_tick_starts_rebuild_in_background(hass, setup_entry, freezer):
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    history = setup_entry.runtime_data.history
    assert not history.days  # setup itself never runs the full rescore
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass, datetime.now().astimezone())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert history.days and not history.stale
    state = hass.states.get("sensor.better_weather_nice_day_rank")
    assert state.state not in ("unknown", "unavailable")


def test_neighbors_window_and_histogram():
    from custom_components.better_weather.history import histogram

    days = {f"2026-10-{i:02d}": {"score": float(s)} for i, s in enumerate([98.2, 90, 95, 40, 70, 97.4, 98.1, 12], 1)}
    n, attrs = history_summary(days)
    nb = attrs["neighbors"]
    # Latest day (Oct 8, 12) is the worst: window stays full, ending at it.
    assert len(nb) == 5 and nb[-1]["date"] == "2026-10-08" and nb[-1]["rank"] == 8
    assert [d["rank"] for d in nb] == sorted(d["rank"] for d in nb)
    days["2026-10-09"] = {"score": 98.15}
    nb = history_summary(days)[1]["neighbors"]
    assert [d["rank"] for d in nb] == [1, 2, 3, 4, 5] and nb[1]["date"] == "2026-10-09"
    h = histogram(days)
    assert len(h) == 101 and sum(h) == len(days) and h[98] == 3 and h[12] == 1

"""Every measured station day, scored with today's rules and ranked ("how does today stack up?").

Long-term recorder statistics are never purged, so the whole station history can be rescored. Day scores
use the same observed-hours path as the skill log's grading, so "measured 97" and the rank agree.
"""

import json
from datetime import datetime, time, timedelta

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DEFAULTS, DOMAIN
from .correction import frozen_days

STORE_VERSION = 1
CHUNK = timedelta(days=31)
BACKFILL_DAYS = 3 * 366
CATCHUP_DAYS = 7
DAY_KEYS = ("nice_wake_hour", "nice_sleep_hour", "day_rain_cap", "rain_hour_min_in")


def scoring_signature(cfg):
    """Options that change a day's score. A change rescores the whole history.

    The learned clear-sky curve is deliberately left out: it moves a little every day, and rescoring a year
    of recorder history daily would cost more than the drift is worth.
    """
    keys = sorted(DEFAULTS) + list(DAY_KEYS)
    return json.dumps({k: cfg.get(k) for k in keys}, sort_keys=True, default=str)


def rank_of(days, key):
    """1 = best. Ties share the better rank."""
    score = days[key]["score"]
    return 1 + sum(1 for d in days.values() if d["score"] > score)


def history_summary(days, limit=10):
    if not days:
        return None, {}
    latest = max(days)
    score = days[latest]["score"]
    ordered = sorted(days.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    return len(days), dict(
        since=min(days),
        latest=dict(
            date=latest,
            score=score,
            rain=days[latest].get("rain", False),
            rank=rank_of(days, latest),
            tied=sum(1 for k, d in days.items() if k != latest and d["score"] == score),
            of=len(days),
        ),
        top_10=[dict(date=k, score=d["score"]) for k, d in ordered[:limit]],
    )


class DayHistory:
    def __init__(self, hass, entry, skill):
        self.hass, self.skill = hass, skill
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.days")
        self.days = {}
        self.signature = None
        self.building = False
        self.tried = set()

    async def async_load(self):
        data = await self.store.async_load() or {}
        self.days = data.get("days", {})
        self.signature = data.get("signature")

    @property
    def stale(self):
        return not self.days or self.signature != scoring_signature(self.skill.cfg)

    @property
    def summary(self):
        return history_summary(self.days)

    def score_range(self, stats, first, last):
        """Complete, non-frozen local days in [first, last) from one recorder slice."""
        tz = self.skill.tz
        frozen = frozen_days(stats.get("temperature", []), tz)
        observed = self.skill.observed_hours(stats)
        out = {}
        for day in sorted({datetime.fromisoformat(k).astimezone(tz).date() for k in observed}):
            if not first <= day < last or day in frozen:
                continue
            scored = self.skill.observed_day(observed, day)
            if scored is not None:
                out[day.isoformat()] = {"score": scored[0], "rain": scored[1]}
        return out

    async def _stats(self, first, last):
        tz = self.skill.tz
        start = datetime.combine(first, time(), tzinfo=tz)
        end = datetime.combine(last, time(), tzinfo=tz)
        # A frozen run that crosses midnight must be visible from both sides.
        return await self.skill.corrections.async_station_statistics(
            start - timedelta(hours=3), end + timedelta(hours=3)
        )

    async def async_rebuild(self):
        """Rescore every day the recorder has, oldest first, one month at a time."""
        if self.building:
            return
        self.building = True
        try:
            today = dt_util.now(self.skill.tz).date()
            first, days = today - timedelta(days=BACKFILL_DAYS), {}
            while first < today:
                last = min(first + CHUNK, today)
                days.update(self.score_range(await self._stats(first, last), first, last))
                first = last
            self.days, self.signature = days, scoring_signature(self.skill.cfg)
            self.tried.clear()
            await self.async_save()
        finally:
            self.building = False

    async def async_finalize(self, target):
        key = target.isoformat()
        if key in self.days:
            return
        stats = await self._stats(target, target + timedelta(days=1))
        scored = self.score_range(stats, target, target + timedelta(days=1))
        if scored:
            self.days.update(scored)
            await self.async_save()

    async def async_tick(self):
        if self.building:
            return
        # A frozen run found later also removes a day that was already ranked.
        dropped = [k for k in (d.isoformat() for d in self.skill.corrections.frozen) if k in self.days]
        for k in dropped:
            del self.days[k]
        if dropped:
            await self.async_save()
        local = dt_util.now(self.skill.tz)
        if local.hour >= self.skill.cfg["nice_sleep_hour"]:
            await self.async_finalize(local.date())     # retried each tick until the recorder has every hour
        for i in range(1, CATCHUP_DAYS + 1):
            day = local.date() - timedelta(days=i)
            if day.isoformat() not in self.days and day not in self.tried:
                self.tried.add(day)                       # a past day that can't be scored is tried once
                await self.async_finalize(day)

    async def async_save(self):
        await self.store.async_save({"signature": self.signature, "days": dict(sorted(self.days.items()))})

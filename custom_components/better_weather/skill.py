"""Persistent forecast snapshots, observations and honest walk-forward grading."""

from datetime import UTC, date, datetime, time, timedelta
from statistics import mean

from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, OPTION_DEFAULTS
from .correction import frozen_days
from .nice import day_score, forecast_score, score_components
from .solar import elevation
from .station import station_values


def skill_summary(records, today):
    """Summarize finalized, non-frozen targets in the trailing 30 local dates."""
    valid = [
        (d, r)
        for d, r in sorted(records.items())
        if today - timedelta(days=29) <= date.fromisoformat(d) <= today
        and r.get("observed") is not None
        and r.get("forecast") is not None
        and not r.get("skipped")
    ]
    n = len(valid)

    def avg(values):
        return round(mean(values), 1) if values else None

    predicted_nice = [r for _, r in valid if r["forecast"] >= 85]
    observed_nice = [r for _, r in valid if r["observed"] >= 85]
    high_pairs = [
        r
        for _, r in valid
        if r.get("high_f") is not None and r.get("observed_high_f") is not None
    ]
    rain_pairs = [r for _, r in valid if r.get("observed_rain") is not None]
    raw_pairs = [r for _, r in valid if r.get("raw_forecast") is not None]
    return dict(
        mae_30d=avg([abs(r["forecast"] - r["observed"]) for _, r in valid]),
        raw_mae_30d=avg([abs(r["raw_forecast"] - r["observed"]) for r in raw_pairs]),
        n_days=n,
        high_temp_mae_f=avg(
            [abs(r["high_f"] - r["observed_high_f"]) for r in high_pairs]
        ),
        rain_hit_rate=mean(
            r["predicted_rain"] == r["observed_rain"] for r in rain_pairs
        )
        if rain_pairs
        else None,
        nice_day_precision=mean(r["observed"] >= 85 for r in predicted_nice)
        if predicted_nice
        else None,
        nice_day_recall=mean(r["forecast"] >= 85 for r in observed_nice)
        if observed_nice
        else None,
        last_10=[
            dict(
                date=d,
                forecast=r["forecast"],
                raw_forecast=r.get("raw_forecast"),
                observed=r["observed"],
            )
            for d, r in valid[-10:]
        ],
    )


class SkillLog:
    """Per-entry stores; snapshots never change after their target is recorded."""

    def __init__(self, hass, entry, forecast, corrections):
        self.hass, self.entry = hass, entry
        self.forecast, self.corrections = forecast, corrections
        self.cfg = OPTION_DEFAULTS | entry.options
        self.tz = corrections.tz
        self.latitude, self.longitude = entry.data["latitude"], entry.data["longitude"]
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.skill")
        self.observation_store = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.observations"
        )
        self.records = {}
        self.samples = {}

    async def async_load(self):
        self.records = await self.store.async_load() or {}
        self.samples = await self.observation_store.async_load() or {}

    @property
    def score_cfg(self):
        """Scoring options plus the station's learned clear-sky curve (sun factor)."""
        return self.cfg | {"clear_sky_curve": self.corrections.clear_sky_curve}

    @property
    def forecast_cfg(self):
        """Forecast hours: the model can't see the station's horizon, so judge its sun as cloudiness.

        Corrected forecast irradiance is on the station's midday scale (the ratio is learned at >=20
        deg), so compare it with the station's clear-sky level over those same angles, flat.
        """
        curve = self.corrections.clear_sky_curve
        calibrated = any(
            not m.get("shortwave_radiation", {}).get("insufficient_data", True)
            for m in (self.corrections.data or {}).values()
        )
        high = [f for e, f in curve if e >= 20]
        if not (calibrated and high):
            return self.cfg
        return self.cfg | {"clear_sky_frac": sum(high) / len(high)}

    @property
    def summary(self):
        return skill_summary(self.records, dt_util.now(self.tz).date())

    def current(self):
        """Station comfort values are metric at the boundary and imperial in the formula."""
        now = dt_util.utcnow()
        values = station_values(self.hass, self.entry.data)
        feels = values.get("feels_like")
        if feels is None:
            feels = values.get("temperature")
        required = [
            feels,
            values.get("dew_point"),
            values.get("wind_speed"),
            values.get("wind_gust"),
            values.get("irradiance"),
            values.get("precipitation_rate"),
        ]
        if not self.corrections.station_stale and all(v is not None for v in required):
            score, attrs = score_components(
                feels * 1.8 + 32,
                values["dew_point"] * 1.8 + 32,
                values["wind_speed"] / 1.609344,
                values["wind_gust"] / 1.609344,
                values["irradiance"],
                values.get("precipitation_rate") or 0,
                elevation(now, self.latitude, self.longitude),
                self.score_cfg,
            )
            return score, attrs | {"source": "station"}
        row = self.current_hour()
        scored = (
            forecast_score(row, self.latitude, self.longitude, self.forecast_cfg)
            if row
            else None
        )
        return (
            (scored[0], scored[1] | {"source": "forecast"})
            if scored
            else (None, {"source": "forecast"})
        )

    def current_hour(self):
        hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        return next(
            (
                r
                for r in (self.forecast.data or {}).get("hourly", [])
                if r["time"] == hour
            ),
            None,
        )

    @callback
    def sample_current(self):
        score, attrs = self.current()
        if score is None or attrs["source"] != "station":
            return
        hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        key = hour.isoformat()
        sample = self.samples.setdefault(key, {"scores": [], "rain_rates": []})
        sample["scores"].append(score)
        values = station_values(self.hass, self.entry.data)
        sample["rain_rates"].append((values.get("precipitation_rate") or 0) / 25.4)
        cutoff = (hour - timedelta(days=3)).isoformat()
        self.samples = {k: v for k, v in self.samples.items() if k >= cutoff}
        self.observation_store.async_delay_save(lambda: self.samples, 5)

    def observed_hours(self, stats=None, *, explain=False):
        """Build observed hour scores from recorder stats, then prefer live score means.

        explain=True also keeps each stats hour's score_components() attrs as "components" so a day
        made of measured hours can still say why it scored what it did (live sample means only have a
        number, so an overridden hour keeps the components of its recorder-mean conditions).
        """
        stats = self.corrections.stats if stats is None else stats
        indexed = {key: {r["start"]: r for r in rows} for key, rows in stats.items()}
        result = {}
        for row in stats.get("temperature", []):
            timestamp = row["start"]
            t = datetime.fromtimestamp(timestamp, UTC)
            if t.astimezone(self.tz).date() in self.corrections.frozen:
                continue

            def value(key, stat="mean"):
                return indexed.get(key, {}).get(timestamp, {}).get(stat)

            feels = value("feels_like")
            if feels is None:
                feels = value("temperature")
            dew, wind, gust, irr = (
                value("dew_point"),
                value("wind_speed"),
                value("wind_gust", "max"),
                value("irradiance"),
            )
            rain_rate = value("precipitation_rate")
            if any(v is None for v in (feels, dew, wind, gust, irr, rain_rate)):
                continue
            rain = rain_rate / 25.4
            score, comp = score_components(
                feels * 1.8 + 32,
                dew * 1.8 + 32,
                wind / 1.609344,
                gust / 1.609344,
                irr,
                rain,
                elevation(t + timedelta(minutes=30), self.latitude, self.longitude),
                self.score_cfg,
            )
            result[t.isoformat()] = {
                "score": score,
                "rain": rain >= self.cfg["rain_hour_min_in"],
            }
            if explain:
                result[t.isoformat()]["components"] = comp
        if stats is self.corrections.stats:
            for key, sample in self.samples.items():
                if (
                    datetime.fromisoformat(key).astimezone(self.tz).date()
                    not in self.corrections.frozen
                ):
                    prior = result.get(key) or {}
                    result[key] = {
                        "score": mean(sample["scores"]),
                        "rain": mean(sample["rain_rates"])
                        >= self.cfg["rain_hour_min_in"],
                    }
                    if prior.get("components"):
                        result[key]["components"] = prior["components"]
        return result

    def observed_day(self, observed, day):
        """(score, rainy) for a fully measured waking day, else None. Shared by grading and ranking."""
        wake, sleep = self.cfg["nice_wake_hour"], self.cfg["nice_sleep_hour"]
        hours = [
            v
            for k, v in observed.items()
            if (t := datetime.fromisoformat(k).astimezone(self.tz)).date() == day
            and wake <= t.hour < sleep
        ]
        if len(hours) < sleep - wake:
            return None
        score = mean(min(v["score"], 10) if v["rain"] else v["score"] for v in hours)
        rainy = any(v["rain"] for v in hours)
        if rainy:
            score = min(score, self.cfg["day_rain_cap"])
        return round(score, 1), rainy

    def score_day(self, day, *, raw=False):
        rows = (self.forecast.data or {}).get("raw_hourly" if raw else "hourly", [])
        now = dt_util.utcnow() if day == dt_util.now(self.tz).date() else None
        # Add observed-only hours if the current API response no longer covers them.
        observed = self.observed_hours(explain=True) if now is not None else {}
        by_hour = {r["time"]: r for r in rows}
        for key in observed:
            t = datetime.fromisoformat(key)
            if t not in by_hour:
                by_hour[t] = {"time": t}
        return day_score(
            sorted(by_hour.values(), key=lambda r: r["time"]),
            day,
            self.tz,
            self.latitude,
            self.longitude,
            self.forecast_cfg,
            observed,
            now,
        )

    async def async_snapshot(self, target):
        key = target.isoformat()
        if key in self.records:
            return
        forecast, attrs = self.score_day(target)
        raw, raw_attrs = self.score_day(target, raw=True)
        expected = self.cfg["nice_sleep_hour"] - self.cfg["nice_wake_hour"]
        if (
            forecast is None
            or raw is None
            or len(attrs["hourly"]) < expected
            or len(raw_attrs["hourly"]) < expected
        ):
            return
        self.records[key] = dict(
            forecast=forecast,
            raw_forecast=raw,
            high_f=attrs["high_f"],
            low_f=attrs["low_f"],
            predicted_rain=attrs["rain_hours"] > 0,
            captured_at=dt_util.utcnow().isoformat(),
        )
        await self.async_save()

    async def async_finalize(self, target):
        key = target.isoformat()
        record = self.records.get(key)
        if not record or record.get("skipped"):
            return
        if target in self.corrections.frozen:
            record["skipped"] = "frozen_station"
            await self.async_save()
            return
        if record.get("observed") is not None:
            return
        # Include adjacent hours so frozen runs crossing midnight are recognized.
        start = datetime.combine(target, time(), tzinfo=self.tz)
        stats = await self.corrections.async_station_statistics(
            start - timedelta(hours=3), start + timedelta(days=1, hours=3)
        )
        if target in frozen_days(stats.get("temperature", []), self.tz):
            record["skipped"] = "frozen_station"
            await self.async_save()
            return
        scored = self.observed_day(self.observed_hours(stats), target)
        if scored is None:
            return
        observed_score, rainy = scored
        highs = [
            r["max"]
            for r in stats.get("temperature", [])
            if r.get("max") is not None
            and datetime.fromtimestamp(r["start"], self.tz).date() == target
        ]
        record.update(
            observed=observed_score,
            observed_high_f=max(highs) * 1.8 + 32 if highs else None,
            observed_rain=rainy,
        )
        await self.async_save()

    async def async_tick(self, *, recent=True):
        if recent:
            await self.corrections.async_recent_statistics()
        self.sample_current()
        local = dt_util.now(self.tz)
        if local.hour >= 20:
            await self.async_snapshot(local.date() + timedelta(days=1))
        # Catch up missed finalizations after restart; never manufacture old forecasts.
        for key in list(self.records):
            target = date.fromisoformat(key)
            if target < local.date() or (
                target == local.date() and local.hour >= self.cfg["nice_sleep_hour"]
            ):
                await self.async_finalize(target)

    async def async_save(self):
        self.records = dict(sorted(self.records.items())[-400:])
        await self.store.async_save(self.records)

    async def async_close(self):
        await self.async_save()
        await self.observation_store.async_save(self.samples)

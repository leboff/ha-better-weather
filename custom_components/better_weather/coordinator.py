"""Forecast and recorder-backed correction coordinators."""

import logging
from datetime import datetime, time, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from sqlalchemy.exc import SQLAlchemyError

from .api import parse_members
from .blend import blend_members
from .const import DOMAIN, OPTION_DEFAULTS
from .correction import (
    compute_corrections,
    frozen_days,
    neutral_corrections,
    station_stale,
)
from .station import metric_statistics

_LOGGER = logging.getLogger(__name__)

CORRECTION_INTERVAL = timedelta(days=1)
# A failed or empty correction run retries soon instead of waiting a full day
# (one Open-Meteo hiccup otherwise leaves forecasts uncorrected until tomorrow).
CORRECTION_RETRY = timedelta(minutes=15)


class CorrectionCoordinator(DataUpdateCoordinator):
    """Daily corrections, retaining neutral or last-good corrections on failure."""

    def __init__(self, hass, entry, client):
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} corrections",
            config_entry=entry,
            update_interval=CORRECTION_INTERVAL,
        )
        self.entry = entry
        self.client = client
        self.cfg = OPTION_DEFAULTS | entry.options
        self.tz = ZoneInfo(hass.config.time_zone)
        self.stats = {}
        self.frozen = set()
        self.station_stale = False
        self.status = "insufficient_data"
        self.computed_at = None
        self.warned = False
        self.insufficient_warned = False
        self.data = neutral_corrections(client.models)

    async def async_station_statistics(self, start, end):
        ids = {v for k, v in self.entry.data.items() if k.startswith("station_") and v}
        if not ids:
            return {}
        recorder = get_instance(self.hass)
        raw = await recorder.async_add_executor_job(
            statistics_during_period,
            self.hass,
            dt_util.as_utc(start),
            dt_util.as_utc(end),
            ids,
            "hour",
            None,
            {"mean", "min", "max"},
        )
        meta = await recorder.async_add_executor_job(
            partial(get_metadata, self.hass, statistic_ids=ids)
        )
        units = {sid: m[1].get("unit_of_measurement") for sid, m in meta.items()}
        return metric_statistics(self.hass, self.entry.data, raw, units)

    async def async_recent_statistics(self):
        now = dt_util.utcnow()
        recent = await self.async_station_statistics(now - timedelta(hours=4), now)
        self.station_stale = station_stale(recent.get("temperature", []), now)
        # Merge recent buckets into the daily history for skill finalization.
        for key, rows in recent.items():
            combined = {r["start"]: r for r in self.stats.get(key, [])}
            combined.update({r["start"]: r for r in rows})
            self.stats[key] = sorted(combined.values(), key=lambda r: r["start"])
        self.frozen = frozen_days(self.stats.get("temperature", []), self.tz)

    async def _async_update_data(self):
        now = dt_util.now(self.tz)
        today = now.date()
        window = self.cfg["correction_window_days"]
        try:
            self.stats = await self.async_station_statistics(
                datetime.combine(
                    today - timedelta(days=window + 1), time(), tzinfo=self.tz
                ),
                now,
            )
            self.frozen = frozen_days(self.stats.get("temperature", []), self.tz)
            self.station_stale = station_stale(
                self.stats.get("temperature", []), dt_util.utcnow()
            )
            if not self.cfg["correction_enabled"]:
                self.status = "disabled"
                result = neutral_corrections(self.client.models)
            else:
                payload = await self.client.previous_runs(today, window)
                result = compute_corrections(
                    parse_members(payload, self.client.models, previous=True),
                    self.stats,
                    today,
                    window,
                    self.tz,
                    self.entry.data["latitude"],
                    self.entry.data["longitude"],
                    frozen=self.frozen,
                )
                self.status = (
                    "insufficient_data"
                    if any(
                        v["insufficient_data"]
                        for m in result.values()
                        for v in m.values()
                    )
                    else "ok"
                )
            if self.status != "disabled" and not any(
                v["n"] for m in result.values() for v in m.values()
            ):
                # Nothing paired at all (station history not readable yet): keep
                # the last good corrections and try again shortly.
                if not self.warned:
                    _LOGGER.warning(
                        "No station samples paired with forecasts; keeping last good corrections, retrying in %s",
                        CORRECTION_RETRY,
                    )
                    self.warned = True
                self.status = "error"
                self.update_interval = CORRECTION_RETRY
                return self.data
            if self.status == "insufficient_data" and not self.insufficient_warned:
                _LOGGER.warning(
                    "Some corrections have insufficient station history; neutral corrections will be used for those variables"
                )
                self.insufficient_warned = True
            self.computed_at = dt_util.utcnow().isoformat()
            self.warned = False
            self.update_interval = CORRECTION_INTERVAL
            return result
        except (
            HomeAssistantError,
            SQLAlchemyError,
            ValueError,
            TypeError,
            OSError,
        ) as err:
            self.status = "error"
            self.update_interval = CORRECTION_RETRY
            if not self.warned:
                _LOGGER.warning(
                    "Correction refresh failed; using last good or neutral corrections, retrying in %s: %s",
                    CORRECTION_RETRY,
                    err,
                )
                self.warned = True
            return self.data


class ForecastCoordinator(DataUpdateCoordinator):
    """Fetch every 30 minutes; the coordinator retains data after a failed request."""

    def __init__(self, hass, entry, client, corrections):
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} forecast",
            config_entry=entry,
            update_interval=timedelta(minutes=30),
        )
        self.client = client
        self.corrections = corrections
        self.members = None
        self.last_fetch = None

    def blended_data(self):
        return {
            "hourly": blend_members(self.members, self.corrections.data),
            "raw_hourly": blend_members(self.members),
        }

    async def _async_update_data(self):
        payload = await self.client.forecast()
        try:
            members = parse_members(payload, self.client.models)
            if not any(
                r.get("temperature_2m") is not None
                for rows in members.values()
                for r in rows.values()
            ):
                raise ValueError("No forecast temperatures")
        except (KeyError, ValueError, TypeError) as err:
            raise UpdateFailed(f"Invalid forecast response: {err}") from err
        self.members = members
        self.last_fetch = dt_util.utcnow().isoformat()
        return self.blended_data()

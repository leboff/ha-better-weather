"""Open-Meteo requests and parsing; every row uses an aware UTC hour start."""

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import CORRECTION_VARS, PRECEDING, VARIABLES

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
_LOGGER = logging.getLogger(__name__)


def number(value):
    """Accept only finite numbers (including values represented as strings)."""
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def utc_time(value):
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_members(payload, models, *, previous=False):
    """Parse members, shifting preceding-hour values onto the described hour."""
    hourly = payload["hourly"]
    times = [utc_time(t) for t in hourly["time"]]
    variables = CORRECTION_VARS if previous else VARIABLES
    result = {}
    for model in models:
        rows = {t: {"time": t} for t in times}
        for var in variables:
            key = f"{var}{'_previous_day1' if previous else ''}_{model}"
            # Open-Meteo also returns unsuffixed variables for a single model.
            values = hourly.get(
                key,
                hourly.get(f"{var}{'_previous_day1' if previous else ''}", [])
                if len(models) == 1
                else [],
            )
            for i, t in enumerate(times):
                target = t - timedelta(hours=1) if var in PRECEDING else t
                rows.setdefault(target, {"time": target})[var] = (
                    number(values[i]) if i < len(values) else None
                )
        result[model] = rows
    return result


class OpenMeteoClient:
    """Use HA's shared aiohttp session."""

    def __init__(self, session, latitude, longitude, models):
        self.session = session
        self.latitude = latitude
        self.longitude = longitude
        self.models = models

    async def _get(self, url, **params):
        params.update(
            latitude=self.latitude,
            longitude=self.longitude,
            models=",".join(self.models),
            timezone="UTC",
            wind_speed_unit="kmh",
        )
        _LOGGER.debug("Fetching Open-Meteo %s", url)
        try:
            async with self.session.get(
                url, params=params, timeout=ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                result = await response.json()
                if (
                    not isinstance(result, dict)
                    or not isinstance(result.get("hourly"), dict)
                    or not isinstance(result["hourly"].get("time"), list)
                    or not result["hourly"]["time"]
                ):
                    raise ValueError("Missing hourly data")
                return result
        except (
            ClientError,
            asyncio.TimeoutError,
            ValueError,
            KeyError,
            TypeError,
        ) as err:
            detail = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
            raise UpdateFailed(f"Open-Meteo request failed: {detail}") from err

    async def forecast(self):
        return await self._get(
            FORECAST_URL, hourly=",".join(VARIABLES), forecast_days=8
        )

    async def previous_runs(self, today, window):
        return await self._get(
            PREVIOUS_URL,
            hourly=",".join(f"{v}_previous_day1" for v in CORRECTION_VARS),
            start_date=str(today - timedelta(days=window + 1)),
            end_date=str(today - timedelta(days=1)),
        )

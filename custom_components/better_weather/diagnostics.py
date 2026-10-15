"""Config-entry diagnostics without coordinates or station identifiers."""

from datetime import timedelta

from homeassistant.util import dt as dt_util


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    cc = runtime.corrections
    today = dt_util.now(cc.tz).date()
    return {
        "models": runtime.forecast.client.models,
        "corrections": cc.data,
        "correction_status": cc.status,
        "computed_at": cc.computed_at,
        "last_fetch": runtime.forecast.last_fetch,
        "station_stale": cc.station_stale,
        "frozen_days_in_window": sorted(
            d.isoformat()
            for d in cc.frozen
            if today - timedelta(days=cc.cfg["correction_window_days"]) <= d < today
        ),
        "skill_summary": runtime.skill.summary,
    }

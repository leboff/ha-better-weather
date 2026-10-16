"""Pure flat corrections and frozen-station detection. Statistics are metric."""

from datetime import datetime, timedelta
from statistics import mean

from .const import CORRECTION_VARS
from .solar import elevation


def is_flat(row):
    """Use the original station-unit test if conversion supplied it."""
    return row.get(
        "flat",
        row.get("max") is not None
        and row.get("min") is not None
        and abs(row["max"] - row["min"]) < 0.01,
    )


def frozen_days(temperature_stats, tz):
    """Exclude every local day touched by a consecutive flat run of >=3 hours."""
    frozen, run = set(), []

    def flush():
        if len(run) >= 3:
            frozen.update(datetime.fromtimestamp(r["start"], tz).date() for r in run)

    for row in sorted(temperature_stats, key=lambda r: r["start"]):
        if run and row["start"] != run[-1]["start"] + 3600:
            flush()
            run = []
        if is_flat(row):
            run.append(row)
        else:
            flush()
            run = []
    flush()
    return frozen


def station_stale(temperature_stats, now):
    """Only the three most recent completed hourly buckets can mark live data stale."""
    end = now.replace(minute=0, second=0, microsecond=0).timestamp()
    by_start = {r["start"]: r for r in temperature_stats}
    last = [by_start.get(end - hours * 3600) for hours in (3, 2, 1)]
    return all(r and is_flat(r) for r in last)


def neutral_corrections(models):
    return {
        m: {
            v: dict(
                kind=kind,
                value=0.0 if kind == "offset" else 1.0,
                n=0,
                insufficient_data=True,
            )
            for v, (_, kind) in CORRECTION_VARS.items()
        }
        for m in models
    }


def compute_corrections(
    members, stats, today, window, tz, latitude, longitude, *, frozen=None
):
    """Pair normalized forecast hour starts with metric station bucket starts."""
    if frozen is None:
        frozen = frozen_days(stats.get("temperature", []), tz)
    indexed = {key: {r["start"]: r for r in rows} for key, rows in stats.items()}
    result = neutral_corrections(members)
    for model, rows in members.items():
        # Variables this model has data for somewhere in the window. A model that
        # returns data but never this variable (ECMWF gusts) doesn't publish it:
        # that's permanent, not a station-history gap, so it's flagged separately.
        published = {
            var
            for row in rows.values()
            for var in CORRECTION_VARS
            if row.get(var) is not None
        }
        for var, (station_key, kind) in CORRECTION_VARS.items():
            forecasts, observed = [], []
            for t, row in rows.items():
                day = t.astimezone(tz).date()
                if not today - timedelta(days=window) <= day < today or day in frozen:
                    continue
                value = row.get(var)
                obs = (
                    indexed.get(station_key, {})
                    .get(t.timestamp(), {})
                    .get("max" if station_key == "wind_gust" else "mean")
                )
                if value is None or obs is None:
                    continue
                if (
                    var == "shortwave_radiation"
                    and elevation(t + timedelta(minutes=30), latitude, longitude) < 20
                ):
                    continue
                forecasts.append(value)
                observed.append(obs)
            n = len(forecasts)
            sufficient = n >= 72 and (kind == "offset" or sum(forecasts) > 0)
            value = 0.0 if kind == "offset" else 1.0
            if sufficient:
                value = (
                    mean(f - o for f, o in zip(forecasts, observed))
                    if kind == "offset"
                    else max(0.2, min(2.0, sum(observed) / sum(forecasts)))
                )
            result[model][var] = dict(
                kind=kind, value=value, n=n, insufficient_data=not sufficient
            )
            if published and var not in published:
                result[model][var]["unsupported"] = True
    return result


def correction_status(result):
    """ok unless a correction the models actually publish is missing data."""
    return (
        "insufficient_data"
        if any(
            v["insufficient_data"] and not v.get("unsupported")
            for m in result.values()
            for v in m.values()
        )
        else "ok"
    )

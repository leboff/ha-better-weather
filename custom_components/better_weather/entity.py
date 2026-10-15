"""Shared per-entry device metadata."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


def device_info(entry):
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Open-Meteo blend",
        entry_type=DeviceEntryType.SERVICE,
    )

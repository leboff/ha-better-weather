"""UI configuration, options and station reconfiguration."""

try:
    import probatio as vol
except ImportError:
    import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import OpenMeteoClient, parse_members
from .const import (
    DEFAULT_MODELS,
    DEFAULTS,
    DOMAIN,
    MODELS,
    OPTION_DEFAULTS,
    STATION_KEYS,
)


def station_schema(values):
    """Optional suggestions must remain removable on reconfiguration."""
    schema = {}
    for key in STATION_KEYS:
        field = f"station_{key}"
        if key == "temperature":
            marker = vol.Required(
                field, **({"default": values[field]} if values.get(field) else {})
            )
        else:
            marker = vol.Optional(
                field,
                description={"suggested_value": values[field]}
                if values.get(field)
                else {},
            )
        schema[marker] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
    return schema


class BetterWeatherConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{round(user_input['latitude'], 4)}_{round(user_input['longitude'], 4)}"
            )
            self._abort_if_unique_id_configured()
            try:
                client = OpenMeteoClient(
                    async_get_clientsession(self.hass),
                    user_input["latitude"],
                    user_input["longitude"],
                    DEFAULT_MODELS,
                )
                members = parse_members(await client.forecast(), DEFAULT_MODELS)
                if not any(
                    r.get("temperature_2m") is not None
                    for rows in members.values()
                    for r in rows.values()
                ):
                    raise UpdateFailed("No forecast temperatures")
            except (UpdateFailed, ValueError, TypeError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input["name"], data=user_input
                )
        values = user_input or {}
        schema = {
            vol.Required(
                "name", default=values.get("name", "Better Weather")
            ): selector.TextSelector(),
            vol.Required(
                "latitude", default=values.get("latitude", self.hass.config.latitude)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-90, max=90, step="any", mode="box")
            ),
            vol.Required(
                "longitude", default=values.get("longitude", self.hass.config.longitude)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-180, max=180, step="any", mode="box")
            ),
            **station_schema(values),
        }
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            # Replace the sensor subset so optional sensors can be cleared.
            data = {k: v for k, v in entry.data.items() if not k.startswith("station_")}
            return self.async_update_and_abort(entry, data=data | user_input)
        return self.async_show_form(
            step_id="reconfigure", data_schema=vol.Schema(station_schema(entry.data))
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BetterWeatherOptionsFlow()


class BetterWeatherOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get("models"):
                errors["models"] = "no_models"
            c = OPTION_DEFAULTS | user_input
            if (
                c["day_lo"] > c["day_hi"]
                or c["dusk_lo"] > c["dusk_hi"]
                or c["dew_ok"] >= c["dew_bad"]
                or c["wind_ok"] >= c["wind_bad"]
                or c["gust_ok"] >= c["gust_bad"]
                or c["nice_wake_hour"] >= c["nice_sleep_hour"]
                or c["w_temp"] + c["w_dew"] + c["w_wind"] <= 0
            ):
                errors["base"] = "invalid_thresholds"
            if not errors:
                for key in (
                    "correction_window_days",
                    "nice_wake_hour",
                    "nice_sleep_hour",
                ):
                    user_input[key] = int(c[key])
                return self.async_create_entry(title="", data=user_input)
        values = OPTION_DEFAULTS | self.config_entry.options | (user_input or {})
        schema = {
            vol.Required("models", default=values["models"]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=MODELS, multiple=True)
            ),
            vol.Required(
                "correction_window_days", default=values["correction_window_days"]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=14, max=180, step=1, mode="box")
            ),
            vol.Required(
                "correction_enabled", default=values["correction_enabled"]
            ): selector.BooleanSelector(),
        }
        positive = {
            "band_full_sun_elev",
            "cold_falloff",
            "hot_falloff",
            "clear_sky_frac",
        }
        for key in DEFAULTS.keys() | {
            "nice_wake_hour",
            "nice_sleep_hour",
            "day_rain_cap",
            "rain_hour_min_in",
        }:
            low, high, step = (-100, 150, "any")
            if key in positive:
                low = 0.01
            elif key.startswith("w_"):
                low, high = 0, 1
            elif key in ("nice_wake_hour", "nice_sleep_hour"):
                low, high, step = 0, 23 if key == "nice_wake_hour" else 24, 1
            elif key in ("rain_cap", "day_rain_cap"):
                low, high = 0, 100
            elif key in ("rain_hour_min_in", "sun_min_elev"):
                low = 0
            schema[vol.Required(key, default=values[key])] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=low, max=high, step=step, mode="box")
            )
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema), errors=errors
        )

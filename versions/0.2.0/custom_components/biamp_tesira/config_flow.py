from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow, ConfigEntry
from homeassistant.core import callback

from .const import (
    CONF_BLOCK_MAP_PATH,
    CONF_HOST_KEY_CHECK,
    CONF_HOSTNAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_ZONES,
    DEFAULT_HOST_KEY_CHECK,
    DEFAULT_PORT,
    DEFAULT_ZONES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOSTNAME): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1, max=65535)),
        vol.Optional(CONF_HOST_KEY_CHECK, default=DEFAULT_HOST_KEY_CHECK): bool,
        vol.Optional(CONF_BLOCK_MAP_PATH, default=""): str,
    }
)


def _attempt_connection(data: dict[str, Any]) -> str:
    """Try to connect to the DSP and return its serial number. Blocking."""
    from pytesira.dsp import DSP
    from pytesira.transport.ssh import SSH

    dsp = DSP()
    ssh = SSH(
        hostname=data[CONF_HOSTNAME],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        host_key_check=data.get(CONF_HOST_KEY_CHECK, DEFAULT_HOST_KEY_CHECK),
    )
    try:
        dsp.connect(backend=ssh)
        return dsp.serial_number
    finally:
        try:
            dsp.close()
        except Exception:
            pass


class BiampTesiraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Biamp Tesira config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            block_map_path = user_input.get(CONF_BLOCK_MAP_PATH, "").strip()
            data = {**user_input, CONF_BLOCK_MAP_PATH: block_map_path}

            try:
                serial = await self.hass.async_add_executor_job(_attempt_connection, data)
            except Exception as exc:
                _LOGGER.warning("Tesira connection test failed: %s", exc)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Biamp Tesira ({user_input[CONF_HOSTNAME]})",
                    data=data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BiampTesiraOptionsFlow(config_entry)


class BiampTesiraOptionsFlow(OptionsFlow):
    """
    Options flow for runtime-adjustable settings.

    Currently exposes zone mapping: a JSON dict of {zone_name: [start_deg, end_deg]}.
    Azimuth is 0-360° CCW from the Biamp logo. Ranges wrap around 0 when start > end.

    Example:
        {"Podium": [330, 30], "Audience": [30, 150], "Side": [150, 330]}
    """

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current_zones = self._entry.options.get(CONF_ZONES, "")
        if not current_zones:
            current_zones = json.dumps(DEFAULT_ZONES, indent=2)

        if user_input is not None:
            zones_raw = user_input.get(CONF_ZONES, "").strip()
            if zones_raw:
                try:
                    parsed = json.loads(zones_raw)
                    if not isinstance(parsed, dict):
                        raise ValueError("Must be a JSON object")
                    for name, bounds in parsed.items():
                        if not (isinstance(bounds, list) and len(bounds) == 2):
                            raise ValueError(f"Zone '{name}' must have [start, end]")
                        _ = float(bounds[0]), float(bounds[1])  # validate numeric
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    errors[CONF_ZONES] = "invalid_zones"
                    _LOGGER.debug("Invalid zone config: %s", exc)
                else:
                    return self.async_create_entry(data={CONF_ZONES: zones_raw})
            else:
                # Empty = reset to defaults
                return self.async_create_entry(data={})

        schema = vol.Schema(
            {
                vol.Optional(CONF_ZONES, default=current_zones): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

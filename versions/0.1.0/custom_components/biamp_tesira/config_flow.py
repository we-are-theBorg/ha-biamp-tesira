from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from .const import (
    CONF_BLOCK_MAP_PATH,
    CONF_HOST_KEY_CHECK,
    CONF_HOSTNAME,
    DEFAULT_HOST_KEY_CHECK,
    DEFAULT_PORT,
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
        serial = dsp.serial_number
        return serial
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
            # Normalise optional empty string fields.
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

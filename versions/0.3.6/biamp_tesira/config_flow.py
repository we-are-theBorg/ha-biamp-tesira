from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BLOCK_MAP_PATH,
    CONF_ENABLED_BLOCK_TYPES,
    CONF_HOST_KEY_CHECK,
    CONF_HOSTNAME,
    CONF_PRESETS,
    DEFAULT_HOST_KEY_CHECK,
    DEFAULT_PORT,
    DOMAIN,
    SUPPORTED_BLOCK_TYPES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOSTNAME): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(
            int, vol.Range(min=1, max=65535)
        ),
        vol.Optional(CONF_HOST_KEY_CHECK, default=DEFAULT_HOST_KEY_CHECK): bool,
        vol.Optional(CONF_BLOCK_MAP_PATH, default=""): str,
    }
)

_BLOCK_TYPE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=k, label=v)
            for k, v in SUPPORTED_BLOCK_TYPES.items()
        ],
        multiple=True,
        mode=selector.SelectSelectorMode.LIST,
    )
)


def _block_type_schema(current: list[str] | None = None) -> vol.Schema:
    default = current if current is not None else list(SUPPORTED_BLOCK_TYPES.keys())
    return vol.Schema(
        {
            vol.Optional(CONF_ENABLED_BLOCK_TYPES, default=default): _BLOCK_TYPE_SELECTOR,
        }
    )


def _options_schema(
    current_enabled: list[str] | None = None,
    current_presets: str = "",
) -> vol.Schema:
    enabled = current_enabled if current_enabled is not None else list(SUPPORTED_BLOCK_TYPES.keys())
    return vol.Schema(
        {
            vol.Optional(CONF_ENABLED_BLOCK_TYPES, default=enabled): _BLOCK_TYPE_SELECTOR,
            vol.Optional(CONF_PRESETS, default=current_presets): str,
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

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}
        self._serial: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BiampTesiraOptionsFlow:
        return BiampTesiraOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            block_map_path = user_input.get(CONF_BLOCK_MAP_PATH, "").strip()
            data = {**user_input, CONF_BLOCK_MAP_PATH: block_map_path}

            try:
                serial = await self.hass.async_add_executor_job(
                    _attempt_connection, data
                )
            except Exception as exc:
                _LOGGER.warning("Tesira connection test failed: %s", exc)
                errors["base"] = "cannot_connect"
            else:
                self._connection_data = data
                self._serial = serial
                return await self.async_step_blocks()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_blocks(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(self._serial)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Biamp Tesira ({self._connection_data[CONF_HOSTNAME]})",
                data=self._connection_data,
                options={
                    CONF_ENABLED_BLOCK_TYPES: user_input.get(
                        CONF_ENABLED_BLOCK_TYPES, list(SUPPORTED_BLOCK_TYPES.keys())
                    ),
                },
            )

        return self.async_show_form(
            step_id="blocks",
            data_schema=_block_type_schema(),
        )


class BiampTesiraOptionsFlow(OptionsFlow):
    """Handle Biamp Tesira options (block type filter, zone map)."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_enabled: list[str] = [
            t for t in self._entry.options.get(
                CONF_ENABLED_BLOCK_TYPES, list(SUPPORTED_BLOCK_TYPES.keys())
            )
            if t in SUPPORTED_BLOCK_TYPES
        ]
        current_presets: str = self._entry.options.get(CONF_PRESETS, "")
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current_enabled, current_presets),
        )

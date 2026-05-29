from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_BLOCK_MAP_PATH,
    CONF_HOST_KEY_CHECK,
    CONF_HOSTNAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DEFAULT_PORT,
    DEFAULT_HOST_KEY_CHECK,
)

if TYPE_CHECKING:
    from pytesira.dsp import DSP

_LOGGER = logging.getLogger(__name__)


class BiampTesiraCoordinator:
    """Manages a single Biamp Tesira DSP connection and distributes updates to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.dsp: DSP | None = None
        self.available = False
        self._listeners: list[Callable[[], None]] = []
        self._block_listeners: dict[str, list[Callable[[], None]]] = {}

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Connect to the DSP (runs blocking I/O in executor)."""
        try:
            await self.hass.async_add_executor_job(self._connect)
        except Exception as exc:
            _LOGGER.error("Failed to connect to Tesira DSP: %s", exc)
            raise ConfigEntryNotReady(f"Cannot connect: {exc}") from exc

    def _connect(self) -> None:
        """Blocking connect — must run in an executor thread."""
        from pytesira.dsp import DSP
        from pytesira.transport.ssh import SSH

        cfg = self.entry.data
        block_map_path = cfg.get(CONF_BLOCK_MAP_PATH) or self._default_block_map_path()

        dsp = DSP(block_map=block_map_path if os.path.exists(block_map_path) else None)
        ssh = SSH(
            hostname=cfg[CONF_HOSTNAME],
            username=cfg[CONF_USERNAME],
            password=cfg[CONF_PASSWORD],
            port=cfg.get(CONF_PORT, DEFAULT_PORT),
            host_key_check=cfg.get(CONF_HOST_KEY_CHECK, DEFAULT_HOST_KEY_CHECK),
        )
        dsp.connect(backend=ssh)
        self.dsp = dsp
        self.available = True

        # Persist block map so future startups skip slow re-enumeration.
        try:
            dsp.save_block_map(block_map_path)
        except Exception as exc:
            _LOGGER.warning("Could not save block map: %s", exc)

        self._register_dsp_callbacks()
        _LOGGER.info(
            "Connected to Tesira '%s' (S/N %s, FW %s) with %d blocks",
            dsp.hostname,
            dsp.serial_number,
            dsp.software_version,
            len(dsp.blocks),
        )

    def _default_block_map_path(self) -> str:
        config_dir = self.hass.config.config_dir
        return os.path.join(config_dir, f"biamp_tesira_{self.entry.entry_id}")

    def _register_dsp_callbacks(self) -> None:
        """Register per-block callbacks that relay updates to the HA event loop."""
        assert self.dsp is not None
        for block_id, block in self.dsp.blocks.items():
            block.register_callback(
                lambda b, bid=block_id: self._on_block_update(bid),
                key="ha_coordinator",
            )

    def _on_block_update(self, block_id: str) -> None:
        """Called from a pytesira worker thread; relay to the HA event loop."""
        self.hass.loop.call_soon_threadsafe(self._async_dispatch, block_id)

    # ------------------------------------------------------------------
    # HA-loop side dispatching
    # ------------------------------------------------------------------

    @callback
    def _async_dispatch(self, block_id: str) -> None:
        """Notify block-specific listeners, then global listeners."""
        for cb in list(self._block_listeners.get(block_id, [])):
            cb()
        for cb in list(self._listeners):
            cb()

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to all coordinator updates. Returns an unsubscribe callable."""
        self._listeners.append(update_callback)

        @callback
        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def async_add_block_listener(
        self, block_id: str, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Subscribe to updates for a specific DSP block. Returns an unsubscribe callable."""
        self._block_listeners.setdefault(block_id, []).append(update_callback)

        @callback
        def remove() -> None:
            try:
                self._block_listeners[block_id].remove(update_callback)
            except ValueError:
                pass

        return remove

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Disconnect from the DSP (runs blocking I/O in executor)."""
        if self.dsp is not None:
            try:
                await self.hass.async_add_executor_job(self._disconnect)
            except Exception as exc:
                _LOGGER.warning("Error during DSP disconnect: %s", exc)

    def _disconnect(self) -> None:
        """Blocking disconnect — must run in an executor thread."""
        if self.dsp is not None:
            self.dsp.close()
            self.dsp = None
        self.available = False

from __future__ import annotations

import json
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
    CONF_ZONES,
    DEFAULT_HOST_KEY_CHECK,
    DEFAULT_PORT,
    DEFAULT_ZONES,
    EVENT_TALKER_LOCATION,
    PARLE_AZ_DEBOUNCE_DEG,
)

if TYPE_CHECKING:
    from pytesira.dsp import DSP

_LOGGER = logging.getLogger(__name__)

_PARLE_BLOCK_TYPE = "BFMic"


class BiampTesiraCoordinator:
    """Manages a single Biamp Tesira DSP connection and distributes updates to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.dsp: DSP | None = None
        self.available = False
        self._listeners: list[Callable[[], None]] = []
        self._block_listeners: dict[str, list[Callable[[], None]]] = {}
        # Debounce tracking for Parlé talker-location events
        self._parle_last_az: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def parle_block_ids(self) -> set[str]:
        """Block IDs whose pytesira type is ParleBeamtracking."""
        if self.dsp is None:
            return set()
        return {
            bid
            for bid, block in self.dsp.blocks.items()
            if type(block).__name__ == _PARLE_BLOCK_TYPE
        }

    # ------------------------------------------------------------------
    # Zone helpers
    # ------------------------------------------------------------------

    @property
    def zones(self) -> dict[str, list[float]]:
        raw = self.entry.options.get(CONF_ZONES)
        if raw:
            try:
                return json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                pass
        return DEFAULT_ZONES

    def azimuth_to_zone(self, azimuth: float) -> str:
        for name, bounds in self.zones.items():
            start, end = float(bounds[0]), float(bounds[1])
            if start <= end:
                if start <= azimuth <= end:
                    return name
            else:  # wraps around 0° (e.g. North: 315→45)
                if azimuth >= start or azimuth <= end:
                    return name
        return "Unknown"

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        try:
            await self.hass.async_add_executor_job(self._connect)
        except Exception as exc:
            _LOGGER.error("Failed to connect to Tesira DSP: %s", exc)
            raise ConfigEntryNotReady(f"Cannot connect: {exc}") from exc

    def _connect(self) -> None:
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

        try:
            dsp.save_block_map(block_map_path)
        except Exception as exc:
            _LOGGER.warning("Could not save block map: %s", exc)

        parle_count = sum(
            1 for b in dsp.blocks.values() if type(b).__name__ == _PARLE_BLOCK_TYPE
        )
        _LOGGER.info(
            "Connected to Tesira '%s' (S/N %s, FW %s) — %d block(s), %d Parlé",
            dsp.hostname, dsp.serial_number, dsp.software_version,
            len(dsp.blocks), parle_count,
        )

        # Register callbacks on all blocks (including ParleBeamtracking)
        for block_id, block in dsp.blocks.items():
            block.register_callback(
                lambda _, bid=block_id: self._on_block_update(bid),
                key="ha_coordinator",
            )

    def _default_block_map_path(self) -> str:
        return os.path.join(
            self.hass.config.config_dir, f"biamp_tesira_{self.entry.entry_id}"
        )

    def _on_block_update(self, block_id: str) -> None:
        self.hass.loop.call_soon_threadsafe(self._async_dispatch, block_id)

    # ------------------------------------------------------------------
    # HA-loop dispatching
    # ------------------------------------------------------------------

    @callback
    def _async_dispatch(self, block_id: str) -> None:
        # Fire biamp_tesira_talker_location for Parlé beam changes (debounced).
        if block_id in self.parle_block_ids:
            self._maybe_fire_talker_event(block_id)

        for cb in list(self._block_listeners.get(block_id, [])):
            cb()
        for cb in list(self._listeners):
            cb()

    def _maybe_fire_talker_event(self, block_id: str) -> None:
        block = self.dsp.blocks[block_id]  # type: ignore[union-attr]
        az = block.primary_azimuth
        if az is None:
            return
        last = self._parle_last_az.get(block_id)
        if last is not None and abs(az - last) < PARLE_AZ_DEBOUNCE_DEG:
            return
        self._parle_last_az[block_id] = az
        self.hass.bus.async_fire(
            EVENT_TALKER_LOCATION,
            {
                "instance_tag": block_id,
                "azimuth": az,
                "intensity": block.primary_intensity,
                "elevation": block.primary_elevation,
                "active_beams": block.active_beams,
                "talker_count": block.talker_count,
                "zone": self.azimuth_to_zone(az),
            },
        )

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update_callback)

        @callback
        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def async_add_block_listener(
        self, block_id: str, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
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
        if self.dsp is not None:
            try:
                await self.hass.async_add_executor_job(self._disconnect)
            except Exception as exc:
                _LOGGER.warning("Error during DSP disconnect: %s", exc)

    def _disconnect(self) -> None:
        if self.dsp is not None:
            self.dsp.close()
            self.dsp = None
        self.available = False

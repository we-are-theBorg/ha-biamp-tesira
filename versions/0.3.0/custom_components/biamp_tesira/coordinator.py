from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

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

# Seconds between subscription-renewal passes. 5s is too short for large systems
# (105 blocks × many channels = hundreds of serial TTP commands per cycle).
# 30s is a practical floor; the DSP subscription TTL is much longer.
_DEVICE_REFRESH_INTERVAL = 30

# Retry delays for background reconnect: 5s, 10s, 20s, … capped at 5 min
_RETRY_INITIAL = 5
_RETRY_MAX = 300


class BiampTesiraCoordinator:
    """Manages a single Biamp Tesira DSP connection and distributes updates to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.dsp: DSP | None = None
        self.available = False
        self._listeners: list[Callable[[], None]] = []
        self._block_listeners: dict[str, list[Callable[[], None]]] = {}
        self._parle_last_az: dict[str, float] = {}

        # Set by _async_connect_loop when the DSP is fully connected.
        # Platforms await this before building entity lists.
        self._ready_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Ready-state helpers
    # ------------------------------------------------------------------

    @property
    def _ready(self) -> asyncio.Event:
        # Lazy creation ensures we're always on the HA event loop.
        if self._ready_event is None:
            self._ready_event = asyncio.Event()
        return self._ready_event

    async def async_wait_ready(self) -> None:
        """Await until the DSP is connected and all blocks are loaded."""
        await self._ready.wait()

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def parle_block_ids(self) -> set[str]:
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
            else:
                if azimuth >= start or azimuth <= end:
                    return name
        return "Unknown"

    # ------------------------------------------------------------------
    # Setup — non-blocking: starts background connection task
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Kick off the background connection loop and return immediately.

        async_setup_entry will complete while the DSP is still connecting.
        Platform entity-setup tasks await async_wait_ready() before creating
        entities, so HA never sees a blocked setup.
        """
        self.hass.async_create_task(
            self._async_connect_loop(),
            name=f"biamp_tesira_connect_{self.entry.entry_id}",
        )

    # ------------------------------------------------------------------
    # Background connect loop — retries with exponential back-off
    # ------------------------------------------------------------------

    async def _async_connect_loop(self) -> None:
        delay = _RETRY_INITIAL
        attempt = 0
        while True:
            attempt += 1
            try:
                _LOGGER.debug(
                    "Connecting to Tesira DSP at %s (attempt %d)",
                    self.entry.data.get(CONF_HOSTNAME, "?"),
                    attempt,
                )
                await self.hass.async_add_executor_job(self._connect)
                # Success
                self.available = True
                self._ready.set()
                _LOGGER.info(
                    "Tesira coordinator ready (%d blocks)", len(self.dsp.blocks)
                )
                # Wake up any entity that registered a listener before connect
                self._async_notify_all()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.available = False
                _LOGGER.warning(
                    "Tesira DSP connection failed (attempt %d), retrying in %ds: %s",
                    attempt, delay, exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RETRY_MAX)

    # ------------------------------------------------------------------
    # Blocking connect — runs in executor thread
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        from pytesira.dsp import DSP
        from pytesira.transport.ssh import SSH

        cfg = self.entry.data
        block_map_path = cfg.get(CONF_BLOCK_MAP_PATH) or self._default_block_map_path()

        dsp = DSP(
            block_map=block_map_path if os.path.exists(block_map_path) else None,
            device_refresh_interval=_DEVICE_REFRESH_INTERVAL,
        )
        ssh = SSH(
            hostname=cfg[CONF_HOSTNAME],
            username=cfg[CONF_USERNAME],
            password=cfg[CONF_PASSWORD],
            port=cfg.get(CONF_PORT, DEFAULT_PORT),
            host_key_check=cfg.get(CONF_HOST_KEY_CHECK, DEFAULT_HOST_KEY_CHECK),
        )
        dsp.connect(backend=ssh)
        self.dsp = dsp

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

        # Register update callbacks on all blocks
        for block_id, block in dsp.blocks.items():
            block.register_callback(
                lambda _, bid=block_id: self._on_block_update(bid),
                key="ha_coordinator",
            )

    def _default_block_map_path(self) -> str:
        return os.path.join(
            self.hass.config.config_dir, f"biamp_tesira_{self.entry.entry_id}"
        )

    # ------------------------------------------------------------------
    # Block-update callbacks (called from pytesira threads)
    # ------------------------------------------------------------------

    def _on_block_update(self, block_id: str) -> None:
        self.hass.loop.call_soon_threadsafe(self._async_dispatch, block_id)

    @callback
    def _async_dispatch(self, block_id: str) -> None:
        if block_id in self.parle_block_ids:
            self._maybe_fire_talker_event(block_id)
        for cb in list(self._block_listeners.get(block_id, [])):
            cb()
        for cb in list(self._listeners):
            cb()

    @callback
    def _async_notify_all(self) -> None:
        """Notify every registered listener — used on first-connect."""
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

    # ------------------------------------------------------------------
    # Listener registration
    # ------------------------------------------------------------------

    @callback
    def async_add_listener(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
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

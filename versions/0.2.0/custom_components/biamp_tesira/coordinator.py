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
    PARLE_SUB_RATE_MS,
    PARLE_TYPE_PATTERNS,
)

if TYPE_CHECKING:
    from pytesira.dsp import DSP
    from pytesira.util.ttp_response import TTPResponse

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parlé Beamtracking proxy
# ---------------------------------------------------------------------------

class ParleBlockProxy:
    """
    Stand-in for a pytesira Block for Parlé mic blocks that pytesira skips.

    We inject this into pytesira's subscription routing table so the DSP RX
    loop calls our subscription_callback exactly as it would for a real block.
    """

    def __init__(
        self,
        block_id: str,
        block_type: str,
        on_update: Callable[[str], None],
    ) -> None:
        self._block_id = block_id
        self.block_type = block_type
        self._on_update = on_update
        self._logger = logging.getLogger(f"biamp_tesira.parle.{block_id}")

        self.beams: list[dict] = []          # [{azimuth, intensity}, …]
        self.elevations: list[float] = []    # per-beam elevation (FW 4.11.2+)
        self.segments_active: list = []      # coarse zone segments

        self._last_event_az: float | None = None
        self._event_pending = False
        self._event_payload: dict | None = None

    # ------------------------------------------------------------------
    # pytesira RX-loop entry point
    # ------------------------------------------------------------------

    def subscription_callback(self, response: TTPResponse) -> None:
        sub_type = response.subscription_type
        if sub_type == "audioSources":
            self._handle_audio_sources(response.value)
        elif sub_type == "segmentsActive":
            val = response.value
            self.segments_active = val if isinstance(val, list) else [val]

        self._maybe_queue_event()
        self._on_update(self._block_id)

    def _handle_audio_sources(self, value) -> None:
        items = value if isinstance(value, list) else [value]
        self.beams = [
            {
                "azimuth": float(item.get("azimuth", 0)),
                "intensity": float(item.get("intensity", 0)),
            }
            for item in items
            if isinstance(item, dict)
        ]

    def _maybe_queue_event(self) -> None:
        az = self.primary_azimuth
        if az is None:
            return
        if (
            self._last_event_az is None
            or abs(az - self._last_event_az) >= PARLE_AZ_DEBOUNCE_DEG
        ):
            self._last_event_az = az
            self._event_pending = True
            self._event_payload = {
                "instance_tag": self._block_id,
                "azimuth": az,
                "intensity": self.primary_intensity,
                "elevation": self.primary_elevation,
                "active_beams": self.active_beams,
                "talker_count": self.talker_count,
            }

    def pop_event(self) -> dict | None:
        if not self._event_pending:
            return None
        self._event_pending = False
        payload, self._event_payload = self._event_payload, None
        return payload

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def active_beams(self) -> list[dict]:
        from .const import PARLE_ACTIVE_THRESHOLD
        return [b for b in self.beams if b["intensity"] >= PARLE_ACTIVE_THRESHOLD]

    @property
    def talker_count(self) -> int:
        return len(self.active_beams)

    @property
    def primary_beam(self) -> dict | None:
        active = self.active_beams
        return max(active, key=lambda b: b["intensity"]) if active else None

    @property
    def primary_azimuth(self) -> float | None:
        b = self.primary_beam
        return b["azimuth"] if b else None

    @property
    def primary_intensity(self) -> float | None:
        b = self.primary_beam
        return b["intensity"] if b else None

    @property
    def primary_elevation(self) -> float | None:
        if not self.elevations or not self.beams:
            return None
        primary = self.primary_beam
        if primary is None:
            return None
        try:
            return self.elevations[self.beams.index(primary)]
        except (ValueError, IndexError):
            return None


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class BiampTesiraCoordinator:
    """Manages a single Biamp Tesira DSP connection and distributes updates to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.dsp: DSP | None = None
        self.available = False
        self.parle_blocks: dict[str, ParleBlockProxy] = {}
        self._listeners: list[Callable[[], None]] = []
        self._block_listeners: dict[str, list[Callable[[], None]]] = {}

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

        self._register_dsp_callbacks()
        self._discover_parle_blocks()

        _LOGGER.info(
            "Connected to Tesira '%s' (S/N %s, FW %s) — %d blocks, %d Parlé block(s)",
            dsp.hostname, dsp.serial_number, dsp.software_version,
            len(dsp.blocks), len(self.parle_blocks),
        )

    def _default_block_map_path(self) -> str:
        return os.path.join(
            self.hass.config.config_dir, f"biamp_tesira_{self.entry.entry_id}"
        )

    # ------------------------------------------------------------------
    # Standard DSP block callbacks
    # ------------------------------------------------------------------

    def _register_dsp_callbacks(self) -> None:
        assert self.dsp is not None
        for block_id, block in self.dsp.blocks.items():
            block.register_callback(
                lambda _, bid=block_id: self._on_block_update(bid),
                key="ha_coordinator",
            )

    def _on_block_update(self, block_id: str) -> None:
        self.hass.loop.call_soon_threadsafe(self._async_dispatch, block_id)

    # ------------------------------------------------------------------
    # Parlé discovery and subscription injection
    # ------------------------------------------------------------------

    def _discover_parle_blocks(self) -> None:
        """
        pytesira skips block types it has no module for.  We read its internal
        block map to find those gaps, then check if any look like Parlé mics.
        """
        assert self.dsp is not None
        try:
            raw_map: dict = self.dsp._DSP__block_map  # type: ignore[attr-defined]
        except AttributeError:
            _LOGGER.warning("Cannot read raw DSP block map; Parlé discovery skipped.")
            return

        for block_id, info in raw_map.items():
            if block_id in self.dsp.blocks:
                continue
            block_type = str(info.get("type", ""))
            if any(p in block_type.lower() for p in PARLE_TYPE_PATTERNS):
                _LOGGER.info("Discovered Parlé block: %s (%s)", block_id, block_type)
                self._setup_parle_block(block_id, block_type)

    def _setup_parle_block(self, block_id: str, block_type: str) -> None:
        assert self.dsp is not None
        proxy = ParleBlockProxy(block_id, block_type, on_update=self._on_block_update)

        # Query per-beam elevation (FW 4.11.2+). Silently ignored on older firmware.
        try:
            resp = self.dsp.device_command(f'"{block_id}" get lobeData')
            if isinstance(resp.value, list):
                proxy.elevations = [
                    float(item.get("elevation", 0)) if isinstance(item, dict) else 0.0
                    for item in resp.value
                ]
        except Exception:
            pass

        # Inject into pytesira's internal subscription routing table.
        try:
            subscriptions: dict = self.dsp._DSP__subscriptions  # type: ignore[attr-defined]
        except AttributeError:
            _LOGGER.warning("Cannot inject into DSP subscriptions; '%s' skipped.", block_id)
            return

        for sub_type, channel in [("audioSources", 1), ("segmentsActive", 1)]:
            sub_name = f"S_{sub_type}_{channel}_{block_id}"
            sub_cmd = (
                f'"{block_id}" subscribe {sub_type} {channel} "{sub_name}" {PARLE_SUB_RATE_MS}'
            )
            subscriptions[sub_name] = (proxy, block_id, sub_name, sub_cmd)
            try:
                self.dsp.device_command(sub_cmd)
            except Exception as exc:
                _LOGGER.warning("Parlé subscribe failed (%s / %s): %s", block_id, sub_type, exc)

        self.parle_blocks[block_id] = proxy

    # ------------------------------------------------------------------
    # HA-loop dispatching
    # ------------------------------------------------------------------

    @callback
    def _async_dispatch(self, block_id: str) -> None:
        # Fire biamp_tesira_talker_location event for Parlé beams crossing the threshold.
        if block_id in self.parle_blocks:
            payload = self.parle_blocks[block_id].pop_event()
            if payload:
                payload["zone"] = self.azimuth_to_zone(payload["azimuth"])
                self.hass.bus.async_fire(EVENT_TALKER_LOCATION, payload)

        for cb in list(self._block_listeners.get(block_id, [])):
            cb()
        for cb in list(self._listeners):
            cb()

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

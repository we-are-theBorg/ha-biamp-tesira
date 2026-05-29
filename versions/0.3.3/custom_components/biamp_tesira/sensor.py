from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator
from .entity import BiampTesiraBlockEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BiampTesiraCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _async_add_when_ready() -> None:
        await coordinator.async_wait_ready()
        entities: list[SensorEntity] = []

        for block_id in coordinator.parle_block_ids:
            block = coordinator.dsp.blocks[block_id]
            for ch in block.channels.keys():
                try:
                    entities += [
                        BiampParleChannelAzimuthSensor(coordinator, block_id, ch),
                        BiampParleChannelIntensitySensor(coordinator, block_id, ch),
                        BiampParleChannelZoneSensor(coordinator, block_id, ch),
                        BiampParleChannelTalkerCountSensor(coordinator, block_id, ch),
                    ]
                except Exception as exc:
                    _LOGGER.warning(
                        "Skipping Parlé sensors for %s ch%d: %s", block_id, ch, exc
                    )

        for block_id, block in coordinator.dsp.blocks.items():
            block_type = type(block).__name__
            try:
                if block_type == "AudioMeter":
                    for ch in block.levels:
                        label = block.labels.get(ch) or f"Ch{ch}"
                        entities.append(
                            BiampAudioMeterSensor(coordinator, block_id, ch, label)
                        )

                elif block_type == "Compressor":
                    entities.append(BiampCompressorGRSensor(coordinator, block_id))

                elif block_type == "BluetoothControlStatus":
                    entities.append(BiampBtConnectedSensor(coordinator, block_id))
            except Exception as exc:
                _LOGGER.warning("Skipping sensor entities for %s (%s): %s", block_id, block_type, exc)

        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_sensor_{entry.entry_id}"
    )


# ---------------------------------------------------------------------------
# Parlé beamtracking sensors — one set per channel per BFMic block
# ---------------------------------------------------------------------------

class BiampParleChannelAzimuthSensor(BiampTesiraBlockEntity, SensorEntity):
    """Azimuth of the loudest active beam on one Parlé tracking channel (0-360°)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°"
    _attr_icon = "mdi:rotate-360"

    def __init__(
        self, coordinator: BiampTesiraCoordinator, block_id: str, channel_idx: int
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_name = f"{block_id} Ch{channel_idx} Azimuth"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_ch{channel_idx}_azimuth"
        )

    @property
    def native_value(self) -> float | None:
        return self._block.primary_azimuth_for_channel(self._channel_idx)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self._block
        ch = self._channel_idx
        return {
            "all_beams": b.beams_for_channel(ch),
            "active_beams": b.active_beams_for_channel(ch),
            "elevation": b.primary_elevation_for_channel(ch),
            "segments_active": b.channel_segments_active.get(ch, []),
        }


class BiampParleChannelIntensitySensor(BiampTesiraBlockEntity, SensorEntity):
    """Intensity of the loudest active beam on one Parlé tracking channel (0.0–1.0)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:microphone"

    def __init__(
        self, coordinator: BiampTesiraCoordinator, block_id: str, channel_idx: int
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_name = f"{block_id} Ch{channel_idx} Intensity"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_ch{channel_idx}_intensity"
        )

    @property
    def native_value(self) -> float | None:
        return self._block.primary_intensity_for_channel(self._channel_idx)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"all_beams": self._block.beams_for_channel(self._channel_idx)}


class BiampParleChannelZoneSensor(BiampTesiraBlockEntity, SensorEntity):
    """Named zone of the active talker on one Parlé tracking channel."""

    _attr_icon = "mdi:map-marker-radius"

    def __init__(
        self, coordinator: BiampTesiraCoordinator, block_id: str, channel_idx: int
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_name = f"{block_id} Ch{channel_idx} Zone"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_ch{channel_idx}_zone"
        )

    @property
    def native_value(self) -> str | None:
        az = self._block.primary_azimuth_for_channel(self._channel_idx)
        if az is None:
            return None
        return self._coordinator.azimuth_to_zone(az)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ch = self._channel_idx
        return {
            "azimuth": self._block.primary_azimuth_for_channel(ch),
            "zone_map": self._coordinator.zones,
        }


class BiampParleChannelTalkerCountSensor(BiampTesiraBlockEntity, SensorEntity):
    """Number of simultaneous beams above threshold on one Parlé tracking channel."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"

    def __init__(
        self, coordinator: BiampTesiraCoordinator, block_id: str, channel_idx: int
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_name = f"{block_id} Ch{channel_idx} Talker Count"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_ch{channel_idx}_talker_count"
        )

    @property
    def native_value(self) -> int:
        return self._block.talker_count_for_channel(self._channel_idx)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"active_beams": self._block.active_beams_for_channel(self._channel_idx)}


# ---------------------------------------------------------------------------
# AudioMeter channel level sensor
# ---------------------------------------------------------------------------

class BiampAudioMeterSensor(BiampTesiraBlockEntity, SensorEntity):
    """Real-time signal level from an AudioMeter channel (dB, peak or RMS)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "dB"
    _attr_icon = "mdi:waveform"

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        channel_idx: int,
        channel_label: str,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_name = f"{block_id} {channel_label} Level"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_{channel_idx}_meter"
        )

    @property
    def native_value(self) -> float | None:
        try:
            return float(self._block.levels[self._channel_idx])
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        block = self._block
        ch = self._channel_idx
        return {
            "hold_enabled": block.hold_enabled.get(ch),
            "hold_time_ms": block.hold_time.get(ch),
            "indefinite_hold": block.indefinite_hold.get(ch),
        }


# ---------------------------------------------------------------------------
# Compressor gain-reduction sensor
# ---------------------------------------------------------------------------

class BiampCompressorGRSensor(BiampTesiraBlockEntity, SensorEntity):
    """Current gain reduction applied by the compressor (read-only, dB)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "dB"
    _attr_icon = "mdi:compression"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Gain Reduction"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_gain_reduction"
        )

    @property
    def native_value(self) -> float | None:
        try:
            gr = self._block.all_gain_reduction
            if gr:
                return float(gr[0])
        except (AttributeError, IndexError, TypeError):
            pass
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "all_channels": self._block.all_gain_reduction,
            "bypass": self._block.bypass,
        }


# ---------------------------------------------------------------------------
# BluetoothControlStatus connection sensor
# ---------------------------------------------------------------------------

class BiampBtConnectedSensor(BiampTesiraBlockEntity, SensorEntity):
    """Bluetooth adapter connection state and remote device information."""

    _attr_icon = "mdi:bluetooth"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Connected"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_bt_connected"
        )

    @property
    def native_value(self) -> str:
        try:
            return "connected" if self._block.bt_connected else "disconnected"
        except AttributeError:
            return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self._block
        return {
            "device_name": b.device_name,
            "profile": b.profile,
            "connected_device": b.connected_device_name,
            "discoverable": b.discoverable,
        }

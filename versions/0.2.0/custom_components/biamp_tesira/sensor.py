from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator, ParleBlockProxy
from .entity import BiampParleBlockEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BiampTesiraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for block_id in coordinator.parle_blocks:
        entities += [
            BiampParleAzimuthSensor(coordinator, block_id),
            BiampParleIntensitySensor(coordinator, block_id),
            BiampParleZoneSensor(coordinator, block_id),
            BiampParleTalkerCountSensor(coordinator, block_id),
        ]

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Azimuth sensor  — primary direction of the loudest active talker
# ---------------------------------------------------------------------------

class BiampParleAzimuthSensor(BiampParleBlockEntity, SensorEntity):
    """
    Primary azimuth of the most intense active beam (degrees, 0-360°).
    0° = directly in front of the Biamp logo, increasing counter-clockwise.
    None when no beam is above the active threshold.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°"
    _attr_icon = "mdi:rotate-360"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Azimuth"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_azimuth"

    @property
    def native_value(self) -> float | None:
        return self._parle.primary_azimuth

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p: ParleBlockProxy = self._parle
        return {
            "all_beams": p.beams,
            "active_beams": p.active_beams,
            "elevation": p.primary_elevation,
            "segments_active": p.segments_active,
            "block_type": p.block_type,
        }


# ---------------------------------------------------------------------------
# Intensity sensor  — 0.0-1.0 loudness of the dominant active beam
# ---------------------------------------------------------------------------

class BiampParleIntensitySensor(BiampParleBlockEntity, SensorEntity):
    """
    Normalised intensity of the most intense active beam (0.0 – 1.0).
    Beams at or above 0.5 are considered active talkers.
    None when no beam is above the threshold.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:microphone"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Intensity"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_intensity"

    @property
    def native_value(self) -> float | None:
        return self._parle.primary_intensity

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"all_beams": self._parle.beams}


# ---------------------------------------------------------------------------
# Zone sensor  — maps primary azimuth to a named room zone
# ---------------------------------------------------------------------------

class BiampParleZoneSensor(BiampParleBlockEntity, SensorEntity):
    """
    Named zone of the active talker, derived by mapping primary azimuth to the
    configured azimuth→zone ranges.  "Unknown" when no active beam or the
    azimuth falls outside every configured range.  "None" when no talker is active.
    """

    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Zone"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_zone"

    @property
    def native_value(self) -> str | None:
        az = self._parle.primary_azimuth
        if az is None:
            return None
        return self._coordinator.azimuth_to_zone(az)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "azimuth": self._parle.primary_azimuth,
            "zone_map": self._coordinator.zones,
        }


# ---------------------------------------------------------------------------
# Talker count sensor  — how many simultaneous active beams
# ---------------------------------------------------------------------------

class BiampParleTalkerCountSensor(BiampParleBlockEntity, SensorEntity):
    """
    Number of beams currently above the active-talker intensity threshold (≥0.5).
    Useful for detecting whether a room is empty, has one speaker, or multiple.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Talker Count"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_talker_count"

    @property
    def native_value(self) -> int:
        return self._parle.talker_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"active_beams": self._parle.active_beams}

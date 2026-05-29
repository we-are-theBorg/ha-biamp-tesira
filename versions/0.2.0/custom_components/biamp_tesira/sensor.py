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
    entities: list[SensorEntity] = []

    for block_id in coordinator.parle_block_ids:
        entities += [
            BiampParleAzimuthSensor(coordinator, block_id),
            BiampParleIntensitySensor(coordinator, block_id),
            BiampParleZoneSensor(coordinator, block_id),
            BiampParleTalkerCountSensor(coordinator, block_id),
        ]

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# All Parlé sensor entities extend BiampTesiraBlockEntity directly — now that
# ParleBeamtracking is a proper pytesira module, the block lives in
# coordinator.dsp.blocks like any other block type.
# ---------------------------------------------------------------------------

class BiampParleAzimuthSensor(BiampTesiraBlockEntity, SensorEntity):
    """
    Primary azimuth of the loudest active beam (0-360°, CCW from Biamp logo).
    State is None when no beam is above the active-talker threshold (≥0.5).
    Extra attributes expose the full beam array and elevation data.
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
        return self._block.primary_azimuth

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self._block
        return {
            "all_beams": b.beams,
            "active_beams": b.active_beams,
            "elevation": b.primary_elevation,
            "segments_active": b.segments_active,
        }


class BiampParleIntensitySensor(BiampTesiraBlockEntity, SensorEntity):
    """
    Normalised intensity of the loudest active beam (0.0–1.0).
    Beams at or above 0.5 are considered active talkers.
    State is None when no beam is above the threshold.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:microphone"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Intensity"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_intensity"

    @property
    def native_value(self) -> float | None:
        return self._block.primary_intensity

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"all_beams": self._block.beams}


class BiampParleZoneSensor(BiampTesiraBlockEntity, SensorEntity):
    """
    Named zone of the active talker, derived by mapping primary azimuth to the
    azimuth→zone ranges configured in the integration options.
    State is None when no beam is above the active-talker threshold.
    """

    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Zone"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_zone"

    @property
    def native_value(self) -> str | None:
        az = self._block.primary_azimuth
        if az is None:
            return None
        return self._coordinator.azimuth_to_zone(az)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "azimuth": self._block.primary_azimuth,
            "zone_map": self._coordinator.zones,
        }


class BiampParleTalkerCountSensor(BiampTesiraBlockEntity, SensorEntity):
    """
    Number of beams simultaneously above the active-talker intensity threshold.
    Useful for detecting whether a room is empty, has one speaker, or several.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Talker Count"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_talker_count"

    @property
    def native_value(self) -> int:
        return self._block.talker_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"active_beams": self._block.active_beams}

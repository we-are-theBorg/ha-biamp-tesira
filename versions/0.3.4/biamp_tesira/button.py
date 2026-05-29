from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
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
        entities: list[ButtonEntity] = []

        for block_id, block in coordinator.dsp.blocks.items():
            if type(block).__name__ == "Preset":
                try:
                    entities.append(BiampTesiraPresetButton(coordinator, block_id))
                except Exception as exc:
                    _LOGGER.warning("Skipping preset button for %s: %s", block_id, exc)

        if entities:
            _LOGGER.debug("Setting up %d preset button(s)", len(entities))
        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_button_{entry.entry_id}"
    )


class BiampTesiraPresetButton(BiampTesiraBlockEntity, ButtonEntity):
    """Button entity that recalls a named Tesira preset."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_preset_{block_id}"
        self._attr_name = block_id

    async def async_press(self) -> None:
        """Recall the preset on the DSP."""
        ok = await self.hass.async_add_executor_job(self._block.recall)
        if not ok:
            _LOGGER.warning("Preset recall failed: %s", self._block_id)

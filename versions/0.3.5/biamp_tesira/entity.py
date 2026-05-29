from __future__ import annotations

from homeassistant.helpers.entity import Entity, DeviceInfo

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator


class BiampTesiraEntity(Entity):
    """Base class for all Biamp Tesira entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: BiampTesiraCoordinator) -> None:
        self._coordinator = coordinator

    @property
    def available(self) -> bool:
        return self._coordinator.available

    @property
    def device_info(self) -> DeviceInfo:
        dsp = self._coordinator.dsp
        return DeviceInfo(
            identifiers={(DOMAIN, dsp.serial_number)},
            name=dsp.hostname,
            manufacturer="Biamp",
            model="Tesira DSP",
            sw_version=dsp.software_version,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class BiampTesiraBlockEntity(BiampTesiraEntity):
    """Entity tied to a specific DSP block — subscribes only to that block's updates."""

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator)
        self._block_id = block_id

    @property
    def _block(self):
        return self._coordinator.dsp.blocks[self._block_id]

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_block_listener(
                self._block_id, self.async_write_ha_state
            )
        )

from __future__ import annotations

from homeassistant.helpers.entity import Entity, DeviceInfo

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator


class BiampTesiraEntity(Entity):
    """
    Base for all Biamp Tesira entities.
    device_info points to the top-level DSP device.
    """

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
    """
    Entity tied to a specific DSP block.

    Creates a child HA device per block (linked to the parent DSP via
    `via_device`) so that HA groups all controls for the same block together
    in the device list rather than mixing everything under one flat device.

    Works for all block types — standard (LevelControl, MuteControl, …) and
    Parlé (ParleBeamtracking) alike, since both live in dsp.blocks after the
    fork installs the proper pytesira module.
    """

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator)
        self._block_id = block_id

    @property
    def _block(self):
        return self._coordinator.dsp.blocks[self._block_id]

    @property
    def device_info(self) -> DeviceInfo:
        dsp = self._coordinator.dsp
        block = self._block
        return DeviceInfo(
            identifiers={(DOMAIN, f"{dsp.serial_number}_{self._block_id}")},
            name=self._block_id,
            manufacturer="Biamp",
            # Model = pytesira class name (e.g. "LevelControl", "ParleBeamtracking")
            model=type(block).__name__,
            via_device=(DOMAIN, dsp.serial_number),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_block_listener(
                self._block_id, self.async_write_ha_state
            )
        )

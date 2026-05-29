from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfSoundPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator
from .entity import BiampTesiraBlockEntity

if TYPE_CHECKING:
    from pytesira.block.LevelControl import LevelControl
    from pytesira.block.DanteInput import DanteInput
    from pytesira.block.DanteOutput import DanteOutput

_LOGGER = logging.getLogger(__name__)

# Block types that expose per-channel level (all extend BaseLevelMute)
_LEVEL_BLOCK_TYPES = ("LevelControl", "DanteInput", "DanteOutput")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BiampTesiraCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _async_add_when_ready() -> None:
        await coordinator.async_wait_ready()
        entities: list[NumberEntity] = []

        for block_id, block in coordinator.dsp.blocks.items():
            block_type = type(block).__name__
            if block_type in _LEVEL_BLOCK_TYPES:
                for channel_idx in block.channels:
                    channel = block.channels[channel_idx]
                    try:
                        min_l = float(channel.min_level)
                        max_l = float(channel.max_level)
                    except AttributeError:
                        min_l, max_l = -100.0, 12.0
                    try:
                        entities.append(
                            BiampLevelNumber(coordinator, block_id, channel_idx, min_l, max_l)
                        )
                    except Exception as exc:
                        _LOGGER.warning(
                            "Skipping level entity for %s ch%d: %s", block_id, channel_idx, exc
                        )

        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_number_{entry.entry_id}"
    )


class BiampLevelNumber(BiampTesiraBlockEntity, NumberEntity):
    """Number entity representing a DSP channel's output level in dB."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = "dB"
    _attr_native_step = 0.5

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        channel_idx: int,
        min_level: float,
        max_level: float,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_native_min_value = min_level
        self._attr_native_max_value = max_level

        channel = coordinator.dsp.blocks[block_id].channels[channel_idx]
        label = _channel_label(channel, block_id, channel_idx)

        self._attr_name = f"{label} Level"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_{channel_idx}_level"
        )

    @property
    def _channel(self):
        return self._coordinator.dsp.blocks[self._block_id].channels[self._channel_idx]

    @property
    def native_value(self) -> float | None:
        try:
            return float(self._channel.level)
        except (AttributeError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.hass.async_add_executor_job(
            _set_level, self._channel, value
        )


def _set_level(channel, value: float) -> None:
    channel.level = float(value)


def _channel_label(channel, block_id: str, channel_idx: int) -> str:
    try:
        lbl = channel.label
        if lbl:
            return lbl
    except AttributeError:
        pass
    return f"{block_id} Ch{channel_idx}"

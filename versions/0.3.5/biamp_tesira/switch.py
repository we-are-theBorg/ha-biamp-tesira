from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator
from .entity import BiampTesiraBlockEntity

_LOGGER = logging.getLogger(__name__)

# Block types with per-channel mute via BaseLevelMute or BaseLevelMuteNoSubscription.
_CHANNEL_MUTE_TYPES = ("LevelControl", "MuteControl", "DanteInput", "DanteOutput", "AudioOutput")

# Block types that have a top-level muted property (no channel index).
_BLOCK_MUTE_TYPES = ("SourceSelector",)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BiampTesiraCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _async_add_when_ready() -> None:
        await coordinator.async_wait_ready()
        entities: list[SwitchEntity] = []

        for block_id, block in coordinator.dsp.blocks.items():
            block_type = type(block).__name__

            try:
                if block_type in _CHANNEL_MUTE_TYPES:
                    for channel_idx, channel in block.channels.items():
                        try:
                            _ = channel.muted  # probe attribute existence
                        except AttributeError:
                            continue
                        poll = block_type == "AudioOutput"
                        entities.append(
                            BiampChannelMuteSwitch(coordinator, block_id, channel_idx, poll)
                        )

                elif block_type in _BLOCK_MUTE_TYPES:
                    entities.append(BiampBlockMuteSwitch(coordinator, block_id))

            except Exception as exc:
                _LOGGER.warning("Skipping switch entities for %s: %s", block_id, exc)

        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_switch_{entry.entry_id}"
    )


class BiampChannelMuteSwitch(BiampTesiraBlockEntity, SwitchEntity):
    """Switch entity for per-channel mute (on = muted)."""

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        channel_idx: int,
        poll: bool,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_should_poll = poll

        channel = coordinator.dsp.blocks[block_id].channels[channel_idx]
        label = _channel_label(channel, block_id, channel_idx)

        self._attr_name = f"{label} Mute"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_{channel_idx}_mute"
        )

    @property
    def _channel(self):
        return self._coordinator.dsp.blocks[self._block_id].channels[self._channel_idx]

    @property
    def is_on(self) -> bool | None:
        try:
            return bool(self._channel.muted)
        except AttributeError:
            return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(_set_muted, self._channel, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(_set_muted, self._channel, False)

    async def async_update(self) -> None:
        """Refresh polled blocks (AudioOutput has no push subscription)."""
        if self._attr_should_poll:
            block = self._coordinator.dsp.blocks[self._block_id]
            if hasattr(block, "refresh_status"):
                await self.hass.async_add_executor_job(block.refresh_status)


class BiampBlockMuteSwitch(BiampTesiraBlockEntity, SwitchEntity):
    """Switch entity for block-level mute (SourceSelector output mute)."""

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Mute"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_mute"
        )

    @property
    def is_on(self) -> bool | None:
        try:
            return bool(self._block.muted)
        except AttributeError:
            return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(_set_block_muted, self._block, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(_set_block_muted, self._block, False)


def _set_muted(channel, value: bool) -> None:
    channel.muted = value


def _set_block_muted(block, value: bool) -> None:
    block.muted = value


def _channel_label(channel, block_id: str, channel_idx: int) -> str:
    try:
        lbl = channel.label
        if lbl:
            return lbl
    except AttributeError:
        pass
    return f"{block_id} Ch{channel_idx}"

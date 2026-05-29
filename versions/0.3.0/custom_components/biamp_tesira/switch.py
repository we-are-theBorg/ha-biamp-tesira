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

# All block types with per-channel mute (channel.muted)
_CHANNEL_MUTE_TYPES = (
    "LevelControl", "MuteControl", "DanteInput", "DanteOutput", "AudioOutput", "BFMic",
    "AudioInput", "AEC", "AVBPOEAmp",
    "BluetoothInput", "BluetoothOutput",
    "UsbInputEx", "UsbOutputEx",
)
# Subset requiring polling (no push subscription)
_POLLED_MUTE_TYPES = frozenset(("AudioOutput", "AudioInput"))

# Block types with a block-level muted property (SourceSelector)
_BLOCK_MUTE_TYPES = ("SourceSelector",)

# Block types with a block-level bypass property
_BLOCK_BYPASS_TYPES = ("PeakLim", "Compressor", "AudioDelay")

# How many crosspoints before we skip creating crosspoint switch entities
_CROSSPOINT_ENTITY_MAX = 64


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

            if block_type in _CHANNEL_MUTE_TYPES:
                polled = block_type in _POLLED_MUTE_TYPES
                for channel_idx, channel in block.channels.items():
                    try:
                        _ = channel.muted
                    except AttributeError:
                        continue
                    entities.append(
                        BiampChannelMuteSwitch(coordinator, block_id, channel_idx, polled)
                    )

            elif block_type in _BLOCK_MUTE_TYPES:
                entities.append(BiampBlockMuteSwitch(coordinator, block_id))

            if block_type in _BLOCK_BYPASS_TYPES:
                entities.append(BiampBlockBypassSwitch(coordinator, block_id))

            if block_type == "MatrixMixer":
                for in_idx in block.inputs:
                    entities.append(
                        BiampMatrixStripMuteSwitch(
                            coordinator, block_id, in_idx, is_input=True
                        )
                    )
                for out_idx in block.outputs:
                    entities.append(
                        BiampMatrixStripMuteSwitch(
                            coordinator, block_id, out_idx, is_input=False
                        )
                    )
                total = block.num_inputs * block.num_outputs
                if total <= _CROSSPOINT_ENTITY_MAX:
                    for in_idx in range(1, block.num_inputs + 1):
                        for out_idx in range(1, block.num_outputs + 1):
                            entities.append(
                                BiampMatrixCrosspointSwitch(
                                    coordinator, block_id, in_idx, out_idx
                                )
                            )

        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_switch_{entry.entry_id}"
    )


# ---------------------------------------------------------------------------
# Per-channel mute switch
# ---------------------------------------------------------------------------

class BiampChannelMuteSwitch(BiampTesiraBlockEntity, SwitchEntity):
    """Switch entity for per-channel mute (on = muted)."""

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        channel_idx: int,
        polled: bool,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_should_poll = polled

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
        await self.hass.async_add_executor_job(
            self._channel.__setattr__, "muted", True
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(
            self._channel.__setattr__, "muted", False
        )

    async def async_update(self) -> None:
        if self._attr_should_poll:
            block = self._coordinator.dsp.blocks[self._block_id]
            if hasattr(block, "refresh_status"):
                await self.hass.async_add_executor_job(block.refresh_status)


# ---------------------------------------------------------------------------
# Block-level mute switch (SourceSelector)
# ---------------------------------------------------------------------------

class BiampBlockMuteSwitch(BiampTesiraBlockEntity, SwitchEntity):
    """Switch entity for block-level mute (SourceSelector output mute)."""

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
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
        await self.hass.async_add_executor_job(
            self._block.__setattr__, "muted", True
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(
            self._block.__setattr__, "muted", False
        )


# ---------------------------------------------------------------------------
# Block-level bypass switch (PeakLim, Compressor, AudioDelay)
# ---------------------------------------------------------------------------

class BiampBlockBypassSwitch(BiampTesiraBlockEntity, SwitchEntity):
    """Switch entity for block-level bypass (on = bypassed)."""

    def __init__(self, coordinator: BiampTesiraCoordinator, block_id: str) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Bypass"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_bypass"
        )
        self._attr_icon = "mdi:debug-step-over"

    @property
    def is_on(self) -> bool | None:
        try:
            return bool(self._block.bypass)
        except AttributeError:
            return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(
            self._block.set_bypass, True
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(
            self._block.set_bypass, False
        )


# ---------------------------------------------------------------------------
# MatrixMixer input/output strip mute switch
# ---------------------------------------------------------------------------

class BiampMatrixStripMuteSwitch(BiampTesiraBlockEntity, SwitchEntity):
    """Switch entity for a MatrixMixer input or output strip mute."""

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        strip_idx: int,
        is_input: bool,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._strip_idx = strip_idx
        self._is_input = is_input

        block = coordinator.dsp.blocks[block_id]
        strip = block.inputs[strip_idx] if is_input else block.outputs[strip_idx]
        direction = "Input" if is_input else "Output"
        label = strip.label or f"{direction} {strip_idx}"

        self._attr_name = f"{block_id} {label} Mute"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}"
            f"_{'in' if is_input else 'out'}{strip_idx}_mute"
        )

    @property
    def _strip(self):
        block = self._coordinator.dsp.blocks[self._block_id]
        return block.inputs[self._strip_idx] if self._is_input else block.outputs[self._strip_idx]

    @property
    def is_on(self) -> bool | None:
        try:
            return bool(self._strip.muted)
        except AttributeError:
            return None

    async def async_turn_on(self, **kwargs) -> None:
        block = self._coordinator.dsp.blocks[self._block_id]
        fn = block.set_input_mute if self._is_input else block.set_output_mute
        await self.hass.async_add_executor_job(fn, self._strip_idx, True)

    async def async_turn_off(self, **kwargs) -> None:
        block = self._coordinator.dsp.blocks[self._block_id]
        fn = block.set_input_mute if self._is_input else block.set_output_mute
        await self.hass.async_add_executor_job(fn, self._strip_idx, False)


# ---------------------------------------------------------------------------
# MatrixMixer crosspoint routing switch
# ---------------------------------------------------------------------------

class BiampMatrixCrosspointSwitch(BiampTesiraBlockEntity, SwitchEntity):
    """Switch entity for a single MatrixMixer crosspoint (on = routed)."""

    _attr_icon = "mdi:arrow-decision"

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        in_idx: int,
        out_idx: int,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._in_idx = in_idx
        self._out_idx = out_idx

        block = coordinator.dsp.blocks[block_id]
        in_label = block.inputs.get(in_idx)
        out_label = block.outputs.get(out_idx)
        in_name = (in_label.label if in_label and in_label.label else f"In{in_idx}")
        out_name = (out_label.label if out_label and out_label.label else f"Out{out_idx}")

        self._attr_name = f"{block_id} {in_name} → {out_name}"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_xp_{in_idx}x{out_idx}"
        )

    @property
    def is_on(self) -> bool | None:
        try:
            return bool(
                self._block.routing.get(self._in_idx, {}).get(self._out_idx, False)
            )
        except AttributeError:
            return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(
            self._block.set_crosspoint_state, self._in_idx, self._out_idx, True
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(
            self._block.set_crosspoint_state, self._in_idx, self._out_idx, False
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channel_label(channel, block_id: str, channel_idx: int) -> str:
    try:
        lbl = channel.label
        if lbl:
            return lbl
    except AttributeError:
        pass
    return f"{block_id} Ch{channel_idx}"

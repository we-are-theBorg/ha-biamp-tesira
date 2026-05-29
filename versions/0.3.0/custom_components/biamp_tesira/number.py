from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator
from .entity import BiampTesiraBlockEntity

_LOGGER = logging.getLogger(__name__)

# All block types whose channels expose .level / .min_level / .max_level
_LEVEL_BLOCK_TYPES = (
    "LevelControl", "DanteInput", "DanteOutput", "BFMic",
    "AudioInput", "AEC", "AVBPOEAmp",
    "BluetoothInput", "BluetoothOutput",
    "UsbInputEx", "UsbOutputEx",
)
# Subset that have no subscriptions and need periodic polling
_POLLED_LEVEL_TYPES = frozenset(("AudioInput",))

# Block-level numeric parameters: type → list of (attr, unit, min, max, step, label_suffix)
_BLOCK_PARAM_TYPES: dict[str, list[tuple]] = {
    "PeakLim": [
        ("threshold",    "dB",  -40.0, 20.0,   0.1,  "Threshold"),
        ("release_time", "ms",    5.0, 10000.0, 10.0, "Release Time"),
    ],
    "AudioDelay": [
        ("delay",        "ms",    0.0,  250.0,  0.1,  "Delay"),
    ],
    "Compressor": [
        ("attack_time",  "ms",    1.0, 2000.0,  1.0,  "Attack Time"),
        ("release_time", "ms",    5.0, 10000.0, 1.0,  "Release Time"),
        ("makeup_gain",  "dB",    0.0,   12.0,  0.1,  "Makeup Gain"),
    ],
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BiampTesiraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    for block_id, block in coordinator.dsp.blocks.items():
        block_type = type(block).__name__

        # Standard channel-level entities
        if block_type in _LEVEL_BLOCK_TYPES:
            polled = block_type in _POLLED_LEVEL_TYPES
            for channel_idx in block.channels:
                channel = block.channels[channel_idx]
                try:
                    min_l = float(channel.min_level)
                    max_l = float(channel.max_level)
                except AttributeError:
                    min_l, max_l = -100.0, 12.0
                entities.append(
                    BiampLevelNumber(
                        coordinator, block_id, channel_idx, min_l, max_l, polled
                    )
                )

        # MatrixMixer input and output strip levels
        elif block_type == "MatrixMixer":
            for in_idx, inp in block.inputs.items():
                entities.append(
                    BiampMatrixStripLevelNumber(
                        coordinator, block_id,
                        strip_idx=in_idx, is_input=True,
                        min_level=inp.min_level, max_level=inp.max_level,
                    )
                )
            for out_idx, out in block.outputs.items():
                entities.append(
                    BiampMatrixStripLevelNumber(
                        coordinator, block_id,
                        strip_idx=out_idx, is_input=False,
                        min_level=out.min_level, max_level=out.max_level,
                    )
                )

        # Block-level numeric parameters (threshold, delay, etc.)
        if block_type in _BLOCK_PARAM_TYPES:
            for attr, unit, mn, mx, step, label in _BLOCK_PARAM_TYPES[block_type]:
                if hasattr(block, attr):
                    entities.append(
                        BiampBlockParamNumber(
                            coordinator, block_id, attr, unit, mn, mx, step, label
                        )
                    )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Channel-level number (BaseLevelMute / BaseLevelMuteNoSubscription blocks)
# ---------------------------------------------------------------------------

class BiampLevelNumber(BiampTesiraBlockEntity, NumberEntity):
    """Slider for a single DSP channel's output level in dB."""

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
        polled: bool = False,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._channel_idx = channel_idx
        self._attr_should_poll = polled

        channel = coordinator.dsp.blocks[block_id].channels[channel_idx]
        label = _channel_label(channel, block_id, channel_idx)
        self._attr_name = f"{label} Level"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_{channel_idx}_level"
        )
        self._attr_native_min_value = min_level
        self._attr_native_max_value = max_level

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
            self._channel.__setattr__, "level", float(value)
        )

    async def async_update(self) -> None:
        if self._attr_should_poll:
            block = self._coordinator.dsp.blocks[self._block_id]
            if hasattr(block, "refresh_status"):
                await self.hass.async_add_executor_job(block.refresh_status)


# ---------------------------------------------------------------------------
# MatrixMixer input/output strip level
# ---------------------------------------------------------------------------

class BiampMatrixStripLevelNumber(BiampTesiraBlockEntity, NumberEntity):
    """Slider for a MatrixMixer input or output strip level in dB."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = "dB"
    _attr_native_step = 0.5

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        strip_idx: int,
        is_input: bool,
        min_level: float,
        max_level: float,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._strip_idx = strip_idx
        self._is_input = is_input

        block = coordinator.dsp.blocks[block_id]
        strip = block.inputs[strip_idx] if is_input else block.outputs[strip_idx]
        direction = "Input" if is_input else "Output"
        label = strip.label or f"{direction} {strip_idx}"

        self._attr_name = f"{block_id} {label} Level"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}"
            f"_{'in' if is_input else 'out'}{strip_idx}_level"
        )
        self._attr_native_min_value = min_level
        self._attr_native_max_value = max_level

    @property
    def _strip(self):
        block = self._coordinator.dsp.blocks[self._block_id]
        return block.inputs[self._strip_idx] if self._is_input else block.outputs[self._strip_idx]

    @property
    def native_value(self) -> float | None:
        try:
            return float(self._strip.level)
        except (AttributeError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        block = self._coordinator.dsp.blocks[self._block_id]
        if self._is_input:
            await self.hass.async_add_executor_job(
                block.set_input_level, self._strip_idx, float(value)
            )
        else:
            await self.hass.async_add_executor_job(
                block.set_output_level, self._strip_idx, float(value)
            )


# ---------------------------------------------------------------------------
# Block-level numeric parameter (threshold, delay, etc.)
# ---------------------------------------------------------------------------

class BiampBlockParamNumber(BiampTesiraBlockEntity, NumberEntity):
    """Number entity for a single block-level numeric parameter."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
        attr: str,
        unit: str,
        min_val: float,
        max_val: float,
        step: float,
        label_suffix: str,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = attr  # used as key
        self._param_attr = attr

        self._attr_name = f"{block_id} {label_suffix}"
        self._attr_unique_id = (
            f"{coordinator.dsp.serial_number}_{block_id}_{attr}"
        )
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step

    @property
    def native_value(self) -> float | None:
        try:
            return float(getattr(self._block, self._param_attr))
        except (AttributeError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        setter = f"set_{self._param_attr}"
        if hasattr(self._block, setter):
            await self.hass.async_add_executor_job(
                getattr(self._block, setter), float(value)
            )
        else:
            await self.hass.async_add_executor_job(
                setattr, self._block, self._param_attr, float(value)
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channel_label(channel: Any, block_id: str, channel_idx: int) -> str:
    try:
        lbl = channel.label
        if lbl:
            return lbl
    except AttributeError:
        pass
    return f"{block_id} Ch{channel_idx}"

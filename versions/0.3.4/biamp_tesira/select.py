from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator
from .entity import BiampTesiraBlockEntity

_LOGGER = logging.getLogger(__name__)

_NONE_SOURCE = "None"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BiampTesiraCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _async_add_when_ready() -> None:
        await coordinator.async_wait_ready()
        entities: list[SelectEntity] = []

        for block_id, block in coordinator.dsp.blocks.items():
            if type(block).__name__ == "SourceSelector":
                try:
                    entities.append(BiampSourceSelectEntity(coordinator, block_id))
                except Exception as exc:
                    _LOGGER.warning("Skipping select entity for %s: %s", block_id, exc)

        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_select_{entry.entry_id}"
    )


class BiampSourceSelectEntity(BiampTesiraBlockEntity, SelectEntity):
    """Select entity representing the active source on a SourceSelector block."""

    def __init__(
        self,
        coordinator: BiampTesiraCoordinator,
        block_id: str,
    ) -> None:
        super().__init__(coordinator, block_id)
        self._attr_name = f"{block_id} Source"
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_{block_id}_source"

    def _source_options(self) -> list[str]:
        """Return human-readable source labels, including a no-source sentinel."""
        block = self._block
        labels = []
        for idx in sorted(block.sources):
            try:
                lbl = block.sources[idx].label or f"Source {idx}"
            except AttributeError:
                lbl = f"Source {idx}"
            labels.append(lbl)
        return [_NONE_SOURCE] + labels

    def _label_to_index(self, label: str) -> int:
        """Convert a source label back to its 1-based Tesira index."""
        block = self._block
        for idx in sorted(block.sources):
            try:
                lbl = block.sources[idx].label or f"Source {idx}"
            except AttributeError:
                lbl = f"Source {idx}"
            if lbl == label:
                return idx
        return 0  # 0 = no selection

    @property
    def options(self) -> list[str]:
        return self._source_options()

    @property
    def current_option(self) -> str | None:
        block = self._block
        idx = block.selected_source
        if idx == 0:
            return _NONE_SOURCE
        try:
            lbl = block.sources[idx].label
            return lbl if lbl else f"Source {idx}"
        except (KeyError, AttributeError):
            return None

    async def async_select_option(self, option: str) -> None:
        if option == _NONE_SOURCE:
            target_idx = 0
        else:
            target_idx = self._label_to_index(option)
        block = self._block
        await self.hass.async_add_executor_job(_select_source, block, target_idx)


def _select_source(block, index: int) -> None:
    block.selected_source = index

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PRESETS, DOMAIN
from .coordinator import BiampTesiraCoordinator
from .entity import BiampTesiraEntity

_LOGGER = logging.getLogger(__name__)


def _parse_preset_names(raw: str) -> list[str]:
    """Split newline-separated preset names, stripping blanks."""
    return [n.strip() for n in raw.splitlines() if n.strip()]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BiampTesiraCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _async_add_when_ready() -> None:
        await coordinator.async_wait_ready()

        raw = entry.options.get(CONF_PRESETS, "")
        names = _parse_preset_names(raw)
        if not names:
            return

        entities: list[ButtonEntity] = []
        for name in names:
            try:
                entities.append(BiampTesiraPresetButton(coordinator, name))
            except Exception as exc:
                _LOGGER.warning("Skipping preset button for %r: %s", name, exc)

        _LOGGER.debug("Setting up %d preset button(s)", len(entities))
        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_button_{entry.entry_id}"
    )


class BiampTesiraPresetButton(BiampTesiraEntity, ButtonEntity):
    """Button that recalls a named Tesira preset via DEVICE recallPresetByName."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BiampTesiraCoordinator, preset_name: str) -> None:
        super().__init__(coordinator)
        self._preset_name = preset_name
        safe = preset_name.lower().replace(" ", "_")
        self._attr_unique_id = f"{coordinator.dsp.serial_number}_preset_{safe}"
        self._attr_name = preset_name

    async def async_press(self) -> None:
        """Recall the preset on the DSP."""
        if not self._coordinator.dsp:
            _LOGGER.warning("DSP not connected, cannot recall preset %r", self._preset_name)
            return
        ok = await self.hass.async_add_executor_job(
            self._coordinator.dsp.recall_preset_by_name, self._preset_name
        )
        if not ok:
            _LOGGER.warning("Preset recall failed: %r", self._preset_name)

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BiampTesiraCoordinator

PLATFORMS = ["number", "switch", "select", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = BiampTesiraCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Forward platform registrations first — each platform starts a background
    # task that awaits coordinator.async_wait_ready() before building entities.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Kick off background DSP connection (non-blocking, retries with back-off).
    # async_setup returns immediately; entities appear once the DSP connects.
    await coordinator.async_setup()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: BiampTesiraCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok

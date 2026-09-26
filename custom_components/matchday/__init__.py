"""The Matchday integration.

Phase 0 skeleton: config entries can be created, but no entities yet. Data
fetching (OpenLigaDB + ESPN) and the matchday sensor follow in phase 1.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Matchday competition from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Matchday competition."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

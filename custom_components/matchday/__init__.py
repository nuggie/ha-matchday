"""The Matchday integration.

Competitions with an OpenLigaDB source (Bundesliga, 2. Bundesliga, DFB-Pokal,
Premier League) get a matchday sensor fed by OpenLigaDB (fixtures, matchday) and
ESPN (live data, match summaries), leagues also a table sensor (ESPN standings
with live delta). The actions ``matchday.get_matchday`` and
``matchday.get_match_details`` browse the other matchdays. The UEFA
competitions follow in phase 3.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .archive import MatchdayArchive
from .const import COMPETITIONS, CONF_COMPETITION, DOMAIN
from .coordinator import MatchdayConfigEntry, MatchdayCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Matchday actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MatchdayConfigEntry) -> bool:
    """Set up a Matchday competition from a config entry."""
    competition = COMPETITIONS[entry.data[CONF_COMPETITION]]
    if competition.openligadb_shortcut is None:
        raise ConfigEntryError(
            f"{competition.name} is not supported yet (ESPN-only competitions "
            "follow in a later version)"
        )
    coordinator = MatchdayCoordinator(hass, entry, competition)
    await coordinator.archive.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MatchdayConfigEntry) -> bool:
    """Unload a Matchday competition."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.archive.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: MatchdayConfigEntry) -> None:
    """Delete the stored matchdays of a removed competition."""
    await MatchdayArchive.async_remove_store(hass, entry.entry_id)

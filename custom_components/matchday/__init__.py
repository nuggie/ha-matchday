"""The Matchday integration.

Competitions with an OpenLigaDB source (Bundesliga, 2. Bundesliga, DFB-Pokal,
Premier League) get a matchday sensor fed by OpenLigaDB (fixtures, matchday) and
ESPN (live data, match summaries), leagues also a table sensor (ESPN standings
with live delta). The UEFA competitions follow in phase 3.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import COMPETITIONS, CONF_COMPETITION
from .coordinator import MatchdayConfigEntry, MatchdayCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: MatchdayConfigEntry) -> bool:
    """Set up a Matchday competition from a config entry."""
    competition = COMPETITIONS[entry.data[CONF_COMPETITION]]
    if competition.openligadb_shortcut is None:
        raise ConfigEntryError(
            f"{competition.name} is not supported yet (ESPN-only competitions "
            "follow in a later version)"
        )
    coordinator = MatchdayCoordinator(hass, entry, competition)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MatchdayConfigEntry) -> bool:
    """Unload a Matchday competition."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

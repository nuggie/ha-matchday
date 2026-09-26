"""Matchday sensor: the current matchday of a competition."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MatchdayConfigEntry, MatchdayCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MatchdayConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the matchday sensor of a competition."""
    async_add_entities([MatchdaySensor(entry.runtime_data)])


class MatchdaySensor(CoordinatorEntity[MatchdayCoordinator], SensorEntity):
    """State: matchday name ("4. Spieltag"); attributes: the enriched matches."""

    _attr_has_entity_name = True
    _attr_translation_key = "matchday"
    # ``matches`` is 40+ KB on a full matchday, far above the recorder's 16 KB
    # attribute limit, and changes every minute during live matches.
    _unrecorded_attributes = frozenset({"matches"})

    def __init__(self, coordinator: MatchdayCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        competition = coordinator.competition
        self._attr_unique_id = f"{competition.key}_matchday"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, competition.key)},
            name=f"Matchday {competition.name}",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Stay available with the last good data while a source fails."""
        return self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        """Return the matchday name as OpenLigaDB spells it."""
        return self.coordinator.data.name if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the matches in the format of the template sensors."""
        data = self.coordinator.data
        if data is None:
            return None
        return {
            "matches": data.matches,
            "matchday": data.matchday,
            "season": data.season,
            "competition": self.coordinator.competition.key,
        }

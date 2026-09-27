"""Matchday sensors: the current matchday and the live table of a competition."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    """Set up the matchday sensor (and the table sensor of a league)."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [MatchdaySensor(coordinator)]
    if coordinator.competition.has_table:
        entities.append(MatchdayTableSensor(coordinator))
    async_add_entities(entities)


def _device_info(coordinator: MatchdayCoordinator) -> DeviceInfo:
    competition = coordinator.competition
    return DeviceInfo(
        identifiers={(DOMAIN, competition.key)},
        name=f"Matchday {competition.name}",
        entry_type=DeviceEntryType.SERVICE,
    )


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
        self._attr_unique_id = f"{coordinator.competition.key}_matchday"
        self._attr_device_info = _device_info(coordinator)

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


class MatchdayTableSensor(CoordinatorEntity[MatchdayCoordinator], SensorEntity):
    """State: time of the standings; attributes: the table with live delta.

    ``table`` has the format of the ``sensor.<liga>_tabelle_live`` template
    sensors (teamName, shortName, teamIconUrl, matches, won, draw, lost, goals,
    opponentGoals, diff, points, live, live_score, espn_team_id).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "table"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # Changes every minute while matches run; the history is of no use.
    _unrecorded_attributes = frozenset({"table"})

    def __init__(self, coordinator: MatchdayCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.competition.key}_table"
        self._attr_device_info = _device_info(coordinator)

    @property
    def available(self) -> bool:
        """Available once standings arrived; stays with the last table on errors."""
        data = self.coordinator.data
        return data is not None and data.table is not None

    @property
    def native_value(self) -> datetime | None:
        """Return when the ESPN standings were fetched."""
        data = self.coordinator.data
        return data.standings_updated if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the table in the format of the template sensors."""
        data = self.coordinator.data
        if data is None or data.table is None:
            return None
        return {
            "table": data.table,
            "live": data.table_live,
            "matchday": data.matchday,
            "season": data.season,
            "competition": self.coordinator.competition.key,
        }

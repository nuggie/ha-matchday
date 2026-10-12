"""Actions to browse matchdays: ``matchday.get_matchday``, ``get_match_details``.

Both answer with data only (``SupportsResponse.ONLY``) and take the matchday
sensor of a competition as ``entity_id``. The shown (current) matchday comes
from the coordinator; any other from ``MatchdayArchive``.
"""

from __future__ import annotations

import copy
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .api import MatchdayApiError
from .archive import (
    MatchdayArchive,
    UnknownMatchdayError,
    UnknownMatchError,
    details_of,
)
from .const import CONF_COMPETITION, DOMAIN
from .coordinator import MatchdayConfigEntry, MatchdayCoordinator

SERVICE_GET_MATCHDAY = "get_matchday"
SERVICE_GET_MATCH_DETAILS = "get_match_details"

ATTR_MATCHDAY = "matchday"
ATTR_SEASON = "season"
ATTR_MATCH_ID = "match_id"

GET_MATCHDAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_MATCHDAY): vol.All(vol.Coerce(int), vol.Range(min=1, max=99)),
        vol.Optional(ATTR_SEASON): vol.All(
            vol.Coerce(int), vol.Range(min=1963, max=2100)
        ),
    }
)
GET_MATCH_DETAILS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_MATCH_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=2_147_483_647)
        ),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Matchday actions (once, for all config entries)."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MATCHDAY,
        _async_get_matchday,
        schema=GET_MATCHDAY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MATCH_DETAILS,
        _async_get_match_details,
        schema=GET_MATCH_DETAILS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _coordinator(hass: HomeAssistant, entity_id: str) -> MatchdayCoordinator:
    """Return the coordinator behind a matchday sensor.

    Raises:
        ServiceValidationError: if ``entity_id`` is no Matchday matchday sensor
            or its competition is not loaded.
    """
    entity = er.async_get(hass).async_get(entity_id)
    entry: MatchdayConfigEntry | None = None
    if entity is not None and entity.platform == DOMAIN and entity.config_entry_id:
        entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if (
        entity is None
        or entry is None
        or entry.domain != DOMAIN
        or entity.domain != "sensor"
        or entity.unique_id != f"{entry.data.get(CONF_COMPETITION)}_matchday"
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_entity",
            translation_placeholders={"entity_id": entity_id},
        )
    if entry.state is not ConfigEntryState.LOADED or entry.runtime_data.data is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"entity_id": entity_id},
        )
    return entry.runtime_data


def _archive(coordinator: MatchdayCoordinator) -> MatchdayArchive:
    """Return the archive behind browsing; ESPN-only competitions have none yet.

    Raises:
        ServiceValidationError: for a competition without OpenLigaDB (UEFA).
    """
    if coordinator.archive is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="browsing_not_supported",
            translation_placeholders={"competition": coordinator.competition.name},
        )
    return coordinator.archive


def _api_error(err: MatchdayApiError) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="api_error",
        translation_placeholders={"error": str(err)},
    )


async def _async_get_matchday(call: ServiceCall) -> ServiceResponse:
    """Return one matchday of a competition in the sensor's ``matches`` format."""
    coordinator = _coordinator(call.hass, call.data[ATTR_ENTITY_ID])
    competition = coordinator.competition.key
    data = coordinator.data
    season: int = call.data.get(ATTR_SEASON, data.season)
    if ATTR_MATCHDAY in call.data:
        matchday: int = call.data[ATTR_MATCHDAY]
    elif season == data.season:
        matchday = data.matchday
    else:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="matchday_required",
            translation_placeholders={"season": str(season)},
        )

    if (season, matchday) == (data.season, data.matchday):
        name, matches, is_current = data.name, copy.deepcopy(data.matches), True
    else:
        try:
            browsed = await _archive(coordinator).async_get_matchday(season, matchday)
        except UnknownMatchdayError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_matchday",
                translation_placeholders={
                    "competition": coordinator.competition.name,
                    "matchday": str(matchday),
                    "season": str(season),
                },
            ) from err
        except MatchdayApiError as err:
            raise _api_error(err) from err
        name, matches, is_current = browsed.name, browsed.matches, False

    response: dict[str, Any] = {
        "competition": competition,
        "season": season,
        "matchday": matchday,
        "name": name,
        "is_current": is_current,
        "matches": matches,
    }
    return response


async def _async_get_match_details(call: ServiceCall) -> ServiceResponse:
    """Return substitutions, stats, lineups, half-time score, shootout of a match."""
    coordinator = _coordinator(call.hass, call.data[ATTR_ENTITY_ID])
    match_id: int = call.data[ATTR_MATCH_ID]
    current = next(
        (m for m in coordinator.data.matches if m.get("matchID") == match_id), None
    )
    if current is not None:
        details = details_of(current)
    else:
        try:
            details = await _archive(coordinator).async_get_match_details(match_id)
        except UnknownMatchError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_match",
                translation_placeholders={
                    "competition": coordinator.competition.name,
                    "match_id": str(match_id),
                },
            ) from err
        except MatchdayApiError as err:
            raise _api_error(err) from err
    response: dict[str, Any] = {"match_id": match_id, **details}
    return response

"""Data update coordinator: one per followed competition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import MatchdayApiError
from .api.espn import EspnClient, scoreboard_months
from .api.openligadb import OldbMatch, OpenLigaDbClient
from .const import DOMAIN, LOGGER, Competition
from .logic.matchday import (
    INTERVAL_IDLE,
    MatchdayPick,
    any_live,
    pick_matchday,
    round_window,
    should_query_espn,
    update_interval,
)
from .logic.merge import NORMALIZERS, merge_matches

type MatchdayConfigEntry = ConfigEntry[MatchdayCoordinator]


@dataclass(slots=True)
class MatchdayData:
    """The matchday currently shown for a competition."""

    matchday: int
    season: int
    name: str
    matches: list[dict[str, Any]] = field(default_factory=list)
    espn_queried: bool = False

    @property
    def pick(self) -> MatchdayPick:
        """Return the matchday as a pick (to keep it when sources fail)."""
        return MatchdayPick(self.matchday, self.season)


class MatchdayCoordinator(DataUpdateCoordinator[MatchdayData]):
    """Fetch OpenLigaDB + ESPN for one competition and merge them.

    Polling adapts to the matchday: every minute while a match is live or about
    to start, every five minutes around the matchday, every 30 minutes otherwise.
    On errors ``UpdateFailed`` keeps the last good data; an empty or broken
    answer never clears the shown matchday.
    """

    config_entry: MatchdayConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MatchdayConfigEntry,
        competition: Competition,
    ) -> None:
        """Initialize the coordinator for ``competition``."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {competition.key}",
            update_interval=INTERVAL_IDLE,
        )
        if competition.openligadb_shortcut is None:
            raise ValueError(f"{competition.key} has no OpenLigaDB source")
        self.competition = competition
        self._league = competition.openligadb_shortcut
        session = async_get_clientsession(hass)
        self._oldb = OpenLigaDbClient(session)
        self._espn = EspnClient(session)
        self._normalize = NORMALIZERS[competition.team_names]

    async def _async_pick_matchday(self) -> MatchdayPick:
        """Decide which matchday to show."""
        previous = self.data.pick if self.data else None
        # Never switch away from a matchday while one of its matches is running;
        # this also saves the two lookups in the one-minute live cadence.
        if self.data and previous and any_live(self.data.matches):
            return previous

        last_match: OldbMatch | None = None
        next_match: OldbMatch | None = None
        try:
            last_match = await self._oldb.async_get_last_match(self._league)
        except MatchdayApiError as err:
            LOGGER.debug("%s: last match unavailable: %s", self._league, err)
        try:
            next_match = await self._oldb.async_get_next_match(self._league)
        except MatchdayApiError as err:
            LOGGER.debug("%s: next match unavailable: %s", self._league, err)
        return pick_matchday(last_match, next_match, dt_util.now(), previous)

    async def _async_update_data(self) -> MatchdayData:
        """Fetch and merge the shown matchday."""
        pick = await self._async_pick_matchday()
        try:
            oldb_matches = await self._oldb.async_get_match_data(
                self._league, pick.season, pick.group_order_id
            )
        except MatchdayApiError as err:
            raise UpdateFailed(f"OpenLigaDB: {err}") from err
        if not oldb_matches:
            raise UpdateFailed(
                f"OpenLigaDB returned no matches for {self._league} "
                f"{pick.season} matchday {pick.group_order_id}"
            )

        window = round_window(oldb_matches)
        today = dt_util.now().date()
        events: list[dict[str, Any]] = []
        espn_queried = window is not None and should_query_espn(window, today)
        if espn_queried and window is not None:
            try:
                events = await self._espn.async_get_events(
                    self.competition.espn_slug, scoreboard_months(*window)
                )
            except MatchdayApiError as err:
                raise UpdateFailed(f"ESPN: {err}") from err

        matches = merge_matches(
            oldb_matches, events, self._normalize, dt_util.get_default_time_zone()
        )
        self._guard_lost_espn_data(pick, matches, espn_queried)

        group = oldb_matches[0].get("group") or {}
        data = MatchdayData(
            matchday=pick.group_order_id,
            season=pick.season,
            name=str(group.get("groupName") or f"{pick.group_order_id}"),
            matches=matches,
            espn_queried=espn_queried,
        )
        self.update_interval = update_interval(matches, window, today, dt_util.utcnow())
        return data

    def _guard_lost_espn_data(
        self, pick: MatchdayPick, matches: list[dict[str, Any]], espn_queried: bool
    ) -> None:
        """Refuse an update in which ESPN suddenly knows none of the matches.

        ESPN does not drop past events. If a queried scoreboard pairs with none of
        the matches that were paired before (same matchday), the answer is broken;
        keep the last good data instead of falling back to bare OpenLigaDB data.
        """
        previous = self.data
        if (
            espn_queried
            and previous is not None
            and previous.pick == pick
            and any(match.get("espn_id") for match in previous.matches)
            and not any(match.get("espn_id") for match in matches)
        ):
            raise UpdateFailed(
                f"ESPN {self.competition.espn_slug} matched none of the matches "
                "it matched before, keeping the previous data"
            )

"""Data update coordinator: one per followed competition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import MatchdayApiError
from .api.espn import EspnClient, scoreboard_months
from .api.openligadb import OldbMatch, OpenLigaDbClient
from .archive import MatchdayArchive
from .const import DOMAIN, LOGGER, Competition
from .espn_archive import EspnArchive
from .logic.espn_only import Round, annotate_ties, pick_round, round_matches
from .logic.matchday import (
    INTERVAL_IDLE,
    MatchdayPick,
    any_live,
    default_season,
    pick_matchday,
    round_window,
    should_query_espn,
    update_interval,
)
from .logic.merge import NORMALIZERS, merge_matches
from .logic.summary import SummaryTracker
from .logic.table import (
    STANDINGS_PENDING,
    TableTracker,
    build_table,
    live_table,
    standings_interval,
)

type MatchdayConfigEntry = ConfigEntry[MatchdayCoordinator]

# Parallel summary requests (a cup round has 32 matches).
SUMMARY_CONCURRENCY = 4
# Team names and crests from OpenLigaDB hardly ever change.
NAMES_REFRESH = timedelta(hours=6)


@dataclass(slots=True)
class MatchdayData:
    """The matchday currently shown for a competition."""

    matchday: int
    season: int
    name: str
    matches: list[dict[str, Any]] = field(default_factory=list)
    espn_queried: bool = False
    # League table with the live delta; ``None`` for cups and until the first
    # standings arrived.
    table: list[dict[str, Any]] | None = None
    table_live: bool = False
    standings_updated: datetime | None = None
    # All matchdays of the season (``MatchdayArchive.matchdays``).
    matchdays: list[dict[str, Any]] = field(default_factory=list)
    # ESPN-only: the stage of the shown round (``league-phase``, ``round-of-16``,
    # ...); ``None`` for the OpenLigaDB competitions.
    phase: str | None = None

    @property
    def pick(self) -> MatchdayPick:
        """Return the matchday as a pick (to keep it when sources fail)."""
        return MatchdayPick(self.matchday, self.season)


class MatchdayCoordinator(DataUpdateCoordinator[MatchdayData]):
    """Fetch OpenLigaDB + ESPN for one competition and merge them.

    Competitions without OpenLigaDB (UEFA) run on ESPN alone: the season's rounds
    come from all its scoreboard months (``EspnArchive``, every six hours), the
    shown round's matches from the scoreboards of its months, second legs with
    their aggregate (``annotate_ties``).

    Polling adapts to the matchday: every minute while a match is live or about
    to start, every five minutes around the matchday, every 30 minutes otherwise.
    On errors ``UpdateFailed`` keeps the last good data; an empty or broken
    answer never clears the shown matchday.

    Match summaries and the league table ride along: summaries only for running
    and just finished matches (``SummaryTracker``), standings every 5 to 30
    minutes (``standings_interval``), the season's matchday list every six hours
    (``MatchdayArchive`` / ``EspnArchive``). Their failures never fail the update
    (the ESPN-only season only until it is known); the last known
    details, table and matchday list stay.
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
        self.competition = competition
        self._league = competition.openligadb_shortcut
        session = async_get_clientsession(hass)
        self._oldb = OpenLigaDbClient(session) if self._league else None
        self._espn = EspnClient(session)
        self._normalize = NORMALIZERS[competition.team_names]
        self._summaries = SummaryTracker()
        self._table_tracker = TableTracker()
        self._standings: list[dict[str, Any]] | None = None
        self._standings_at: datetime | None = None
        self._standings_season: int | None = None
        # After a failed standings request, wait before asking again.
        self._standings_retry_at: datetime | None = None
        self._names: list[dict[str, Any]] = []
        self._names_at: datetime | None = None
        self._base_table: list[dict[str, Any]] | None = None
        # Other matchdays on request, and the season's matchday list.
        self.archive: MatchdayArchive | EspnArchive = (
            MatchdayArchive(hass, entry, competition, self._oldb, self._espn)
            if self._oldb is not None
            else EspnArchive(hass, entry, competition, self._espn)
        )

    async def _async_pick_matchday(self, oldb: OpenLigaDbClient) -> MatchdayPick:
        """Decide which matchday to show."""
        previous = self.data.pick if self.data else None
        # Never switch away from a matchday while one of its matches is running;
        # this also saves the two lookups in the one-minute live cadence.
        if self.data and previous and any_live(self.data.matches):
            return previous

        last_match: OldbMatch | None = None
        next_match: OldbMatch | None = None
        try:
            last_match = await oldb.async_get_last_match(self._league)
        except MatchdayApiError as err:
            LOGGER.debug("%s: last match unavailable: %s", self._league, err)
        try:
            next_match = await oldb.async_get_next_match(self._league)
        except MatchdayApiError as err:
            LOGGER.debug("%s: next match unavailable: %s", self._league, err)
        return pick_matchday(last_match, next_match, dt_util.now(), previous)

    async def _async_update_data(self) -> MatchdayData:
        """Fetch and merge the shown matchday."""
        if self._oldb is None or isinstance(self.archive, EspnArchive):
            return await self._async_update_espn_only()
        pick = await self._async_pick_matchday(self._oldb)
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
            oldb_matches,
            events,
            self._normalize,
            dt_util.get_default_time_zone(),
            oldb_results=self.competition.oldb_results,
        )
        self._guard_lost_espn_data(pick, matches, espn_queried)
        await self._async_update_summaries(matches)

        group = oldb_matches[0].get("group") or {}
        data = MatchdayData(
            matchday=pick.group_order_id,
            season=pick.season,
            name=str(group.get("groupName") or f"{pick.group_order_id}"),
            matches=matches,
            espn_queried=espn_queried,
        )
        if self.competition.has_table:
            await self._async_update_table(data)
        await self.archive.async_refresh_schedule(pick.season, dt_util.utcnow())
        data.matchdays = self.archive.matchdays(pick.season)
        self.update_interval = update_interval(matches, window, today, dt_util.utcnow())
        return data

    async def _async_update_espn_only(self) -> MatchdayData:
        """Fetch the shown round of an ESPN-only competition."""
        archive = self.archive
        if not isinstance(archive, EspnArchive):
            raise TypeError(f"{self.competition.key} is no ESPN-only competition")
        now = dt_util.utcnow()
        season = default_season(dt_util.now())
        await archive.async_refresh_schedule(season, now)
        rounds = archive.rounds(season)
        if not rounds:
            raise UpdateFailed(f"ESPN {self.competition.espn_slug}: no rounds known")

        # Never switch away from a round while one of its matches is running.
        current: Round | None = None
        if self.data and any_live(self.data.matches):
            current = next((r for r in rounds if r.number == self.data.matchday), None)
        if current is None:
            current = pick_round(rounds, now)
        if current is None:
            raise UpdateFailed(f"ESPN {self.competition.espn_slug}: no round to show")

        months = scoreboard_months(
            current.first_kickoff.date(), current.last_kickoff.date()
        )
        events: list[tuple[str, dict[str, Any]]] = []
        try:
            for slug in current.slugs:
                events.extend(
                    (slug, event)
                    for event in await self._espn.async_get_events(slug, months)
                )
        except MatchdayApiError as err:
            raise UpdateFailed(f"ESPN: {err}") from err
        matches = round_matches(current, events, dt_util.get_default_time_zone())
        if not matches:
            raise UpdateFailed(
                f"ESPN {self.competition.espn_slug} returned none of the matches "
                f"of {current.name}"
            )
        # First legs come from the season, fresh events override it.
        annotate_ties(matches, [*archive.season_events(season), *events])
        await self._async_update_summaries(matches)

        data = MatchdayData(
            matchday=current.number,
            season=season,
            name=current.name,
            matches=matches,
            espn_queried=True,
            matchdays=archive.matchdays(season),
            phase=current.stage,
        )
        if self.competition.has_table:
            await self._async_update_table(data)
        window = round_window(matches)
        self.update_interval = update_interval(
            matches, window, dt_util.now().date(), dt_util.utcnow()
        )
        return data

    async def _async_update_summaries(self, matches: list[dict[str, Any]]) -> None:
        """Fetch the summaries that are due and write all known details."""
        now = dt_util.utcnow()
        due = self._summaries.due(matches, now)
        if due:
            semaphore = asyncio.Semaphore(SUMMARY_CONCURRENCY)

            async def fetch(match: dict[str, Any]) -> None:
                summary: dict[str, Any] | None = None
                # ESPN-only matches name their slug (main or qualifying).
                slug = match.get("espn_slug") or self.competition.espn_slug
                async with semaphore:
                    try:
                        summary = await self._espn.async_get_summary(
                            slug, str(match["espn_id"])
                        )
                    except MatchdayApiError as err:
                        LOGGER.debug("%s: summary unavailable: %s", slug, err)
                self._summaries.store(match, summary, now)

            await asyncio.gather(*(fetch(match) for match in due))
        self._summaries.apply(matches)

    async def _async_update_table(self, data: MatchdayData) -> None:
        """Refresh the standings when due and compute the live table."""
        now = dt_util.utcnow()
        counted = self._table_tracker.counted(data.matches, self._base_table, now)
        interval = standings_interval(
            data.matches, self._table_tracker.has_pending(counted), now
        )
        due = (
            self._standings is None
            or self._standings_season != data.season
            or self._standings_at is None
            or now - self._standings_at >= interval
        )
        if due and (
            self._standings_retry_at is None or now >= self._standings_retry_at
        ):
            await self._async_fetch_standings(data.season, now)

        if self._standings is None:
            return
        self._base_table = build_table(
            self._standings, self._names, data.matches, self._base_table
        )
        counted = self._table_tracker.counted(data.matches, self._base_table, now)
        data.table = live_table(self._base_table, counted)
        data.table_live = any(team["live"] for team in data.table)
        data.standings_updated = self._standings_at

    async def _async_fetch_standings(self, season: int, now: datetime) -> None:
        """Fetch ESPN standings (and OpenLigaDB names when stale); keep old on errors."""
        slug = self.competition.espn_slug
        try:
            entries = await self._espn.async_get_standings(slug, season)
        except MatchdayApiError as err:
            LOGGER.debug("%s: standings unavailable: %s", slug, err)
            self._standings_retry_at = now + STANDINGS_PENDING
            return
        self._standings_retry_at = None
        if self._standings_season != season:
            self._base_table = None
            self._names, self._names_at = [], None
        self._standings, self._standings_at = entries, now
        self._standings_season = season

        if self._oldb is None or self._league is None:
            return
        if self._names_at is None or now - self._names_at >= NAMES_REFRESH:
            try:
                self._names = await self._oldb.async_get_table(self._league, season)
                self._names_at = now
            except MatchdayApiError as err:
                LOGGER.debug("%s: team names unavailable: %s", self._league, err)

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

"""The rounds of an ESPN-only competition (UEFA): season, other rounds, details.

The counterpart of ``MatchdayArchive`` for competitions without OpenLigaDB. The
season's events - every scoreboard month of the main slug (July to June) and of
the qualifying slug (June to August) - are fetched every six hours. They define
the rounds (``logic/espn_only.py``), the ``matchdays`` attribute and the first
legs of two-legged ties.

``matchday.get_matchday`` serves the other rounds from them: settled and future
rounds straight from the season, a round around now from fresh scoreboards (15
minutes in memory). Other seasons are fetched on request and kept in memory for
six hours. ``matchday.get_match_details`` fetches the summary from the slug the
match came from; the details of a settled match stay in memory for a day, of a
running one a minute, others 15 minutes. Nothing is stored on disk: a restart
fetches the season again anyway.

Errors of ESPN surface as ``MatchdayApiError``; nothing here touches the
coordinator's own data.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import MatchdayApiError
from .api.espn import EspnClient, EspnEvent, scoreboard_months
from .archive import (
    CACHE_TTL,
    LIVE_DETAILS_TTL,
    BrowsedMatchday,
    CachingArchive,
    UnknownMatchdayError,
    UnknownMatchError,
    details_of,
)
from .const import LOGGER, Competition
from .logic.espn_only import (
    Round,
    annotate_ties,
    espn_match,
    round_matches,
    season_rounds,
)
from .logic.matchday import LIVE_STATUSES
from .logic.schedule import is_future, match_settled, matchday_settled
from .logic.summary import SUMMARY_STATUSES, parse_summary

SEASON_REFRESH = timedelta(hours=6)
SEASON_RETRY = timedelta(minutes=15)
SETTLED_DETAILS_TTL = timedelta(days=1)

type SlugEvent = tuple[str, EspnEvent]


@dataclass(frozen=True, slots=True)
class EspnSeason:
    """The events of a season and the rounds cut from them."""

    season: int
    fetched: datetime
    rounds: list[Round]
    events: list[SlugEvent]


def season_months(season: int, first_month: int, count: int) -> list[str]:
    """Return ``count`` consecutive ``YYYYMM`` values from ``first_month`` of ``season``."""
    months: list[str] = []
    year, month = season, first_month
    for _ in range(count):
        months.append(f"{year}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


class EspnArchive(CachingArchive):
    """Fetch and cache the season, other rounds and match details (ESPN only)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        competition: Competition,
        espn: EspnClient,
    ) -> None:
        """Initialize without a season."""
        super().__init__(hass, entry, competition)
        self._espn = espn
        self._season: EspnSeason | None = None
        self._season_retry_at: datetime | None = None

    # --- lifecycle ------------------------------------------------------------

    async def async_load(self) -> None:
        """Nothing is stored; the season is fetched with the first update."""

    async def async_unload(self) -> None:
        """Cancel running fetches and drop the memory cache."""
        self._drop_memory()

    # --- the season -------------------------------------------------------------

    def rounds(self, season: int) -> list[Round]:
        """Return the rounds of ``season`` (empty until known)."""
        current = self._season
        return current.rounds if current and current.season == season else []

    def matchdays(self, season: int) -> list[dict[str, Any]]:
        """Return the rounds of ``season`` in the ``matchdays`` format."""
        return [r.as_matchday() for r in self.rounds(season)]

    def season_events(self, season: int) -> list[SlugEvent]:
        """Return the events of ``season`` (empty until known)."""
        current = self._season
        return current.events if current and current.season == season else []

    async def async_refresh_schedule(self, season: int, now: datetime) -> None:
        """Fetch the season when due; failures keep the old one."""
        current = self._season
        if (
            current is not None
            and current.season == season
            and now - current.fetched < SEASON_REFRESH
        ):
            return
        if self._season_retry_at is not None and now < self._season_retry_at:
            return
        try:
            fetched = await self._async_fetch_season(season, now)
        except MatchdayApiError as err:
            LOGGER.debug("%s: season unavailable: %s", self._competition.key, err)
            self._season_retry_at = now + SEASON_RETRY
            return
        if not fetched.rounds:
            LOGGER.debug(
                "%s: season %s has no rounds yet", self._competition.key, season
            )
            self._season_retry_at = now + SEASON_RETRY
            return
        self._season, self._season_retry_at = fetched, None

    async def _async_fetch_season(self, season: int, now: datetime) -> EspnSeason:
        """Fetch every scoreboard month of a season; any failing month fails it.

        A missing month would drop its rounds.
        """
        requests = [(self._competition.espn_slug, season_months(season, 7, 12))]
        if self._competition.espn_qual_slug:
            requests.append(
                (self._competition.espn_qual_slug, season_months(season, 6, 3))
            )
        events: list[SlugEvent] = []
        for slug, months in requests:
            events.extend(
                (slug, event)
                for event in await self._espn.async_get_events(slug, months)
            )
        return EspnSeason(season, now, season_rounds(events), events)

    async def _async_season(self, season: int) -> EspnSeason:
        """Return the current season, or another one (fetched, six hours in memory)."""
        current = self._season
        if current is not None and current.season == season:
            return current
        key = ("season", season)
        cached: EspnSeason | None = self._cached(key)
        if cached is not None:
            return cached
        return await self._shared(key, partial(self._async_fetch_other_season, season))

    async def _async_fetch_other_season(self, season: int) -> EspnSeason:
        result = await self._async_fetch_season(season, dt_util.utcnow())
        self._remember(("season", season), result, SEASON_REFRESH)
        return result

    # --- rounds -------------------------------------------------------------------

    async def async_get_matchday(self, season: int, matchday: int) -> BrowsedMatchday:
        """Return a round in the ``matches`` format (a copy, safe to change).

        Raises:
            UnknownMatchdayError: if the season has no such round.
            MatchdayApiError: if ESPN fails.
        """
        data = await self._async_season(season)
        current = next((r for r in data.rounds if r.number == matchday), None)
        if current is None:
            raise UnknownMatchdayError
        local_tz = dt_util.get_default_time_zone()
        now = dt_util.utcnow()
        events = data.events
        matches = round_matches(current, events, local_tz)
        if not is_future(matches, now) and not matchday_settled(matches, now):
            # Around now: fresh scoreboards, which override the season's events.
            events = [*data.events, *await self._async_round_events(current)]
            matches = round_matches(current, events, local_tz)
        annotate_ties(matches, events)
        return BrowsedMatchday(season, matchday, current.name, copy.deepcopy(matches))

    async def _async_round_events(self, current: Round) -> list[SlugEvent]:
        """Return fresh scoreboard events of a round's months (15 min in memory)."""
        months = scoreboard_months(
            current.first_kickoff.date(), current.last_kickoff.date()
        )
        events: list[SlugEvent] = []
        for slug in current.slugs:
            for month in months:
                key = ("scoreboard", slug, month)
                scoreboard = self._cached(key)
                if scoreboard is None:
                    scoreboard = await self._shared(
                        key, partial(self._async_fetch_scoreboard, slug, month)
                    )
                events.extend((slug, event) for event in scoreboard)
        return events

    async def _async_fetch_scoreboard(self, slug: str, month: str) -> list[EspnEvent]:
        events = await self._espn.async_get_scoreboard(slug, month)
        self._remember(("scoreboard", slug, month), events, CACHE_TTL)
        return events

    # --- match details --------------------------------------------------------

    async def async_get_match_details(self, match_id: int) -> dict[str, Any]:
        """Return the summary fields of a match that is not on the shown round.

        Raises:
            UnknownMatchError: if no known season has such a match.
            MatchdayApiError: if ESPN fails.
        """
        key = ("details", match_id)
        details = self._cached(key) or await self._shared(
            key, partial(self._async_fetch_details, match_id)
        )
        return copy.deepcopy(details)

    def _find_event(self, match_id: int) -> SlugEvent | None:
        """Look a match up in the seasons and scoreboards known.

        The current season, other seasons in memory, then fresh scoreboards in
        memory; a later find wins, so a fresh scoreboard beats a season.
        """
        wanted = str(match_id)
        candidates: list[SlugEvent] = list(self._season.events if self._season else [])
        now = dt_util.utcnow()
        live = [
            (key, value)
            for key, (expires, value) in self._memory.items()
            if now < expires
        ]
        for key, value in live:
            if key[0] == "season" and isinstance(value, EspnSeason):
                candidates.extend(value.events)
        for key, value in live:
            if key[0] == "scoreboard" and isinstance(value, list):
                candidates.extend((str(key[1]), event) for event in value)
        found: SlugEvent | None = None
        for slug, event in candidates:
            if str(event.get("id")) == wanted:
                found = (slug, event)
        return found

    async def _async_fetch_details(self, match_id: int) -> dict[str, Any]:
        found = self._find_event(match_id)
        match = (
            espn_match(found[1], found[0], dt_util.get_default_time_zone())
            if found is not None
            else None
        )
        if found is None or match is None:
            raise UnknownMatchError
        slug = found[0]
        details = details_of(match)
        fetched = False
        if match.get("status") in SUMMARY_STATUSES:
            summary = await self._espn.async_get_summary(slug, str(match["espn_id"]))
            for field, value in parse_summary(
                summary, match.get("espn_home_team_id")
            ).items():
                if value is not None:
                    details[field] = value
            fetched = True
        if fetched and match_settled(match, dt_util.utcnow()):
            ttl = SETTLED_DETAILS_TTL
        elif match.get("status") in LIVE_STATUSES:
            ttl = LIVE_DETAILS_TTL
        else:
            ttl = CACHE_TTL
        self._remember(("details", match_id), details, ttl)
        return details

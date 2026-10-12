"""Matchdays other than the current one: fetching, caching, the season schedule.

The coordinator shows one matchday. ``MatchdayArchive`` serves the others on
request (``matchday.get_matchday`` / ``matchday.get_match_details``) and keeps
the list of all matchdays of the shown season (``matchdays`` attribute).

Caching, per config entry:

- A settled matchday (every match finished, paired with ESPN and kicked off more
  than a day ago) and the details of a settled match are kept for good in a
  ``Store`` (``.storage/matchday.<entry id>``), bounded in size; the oldest
  seasons go first.
- Everything else (running, recent or future matchdays, ESPN scoreboards of a
  month, other seasons' matchday names) is kept in memory for 15 minutes, the
  details of a running match for one minute.
- Concurrent requests for the same matchday, scoreboard or match details share
  one fetch.
- The season schedule (names from ``getavailablegroups``, kickoff range per
  matchday from the season's fixtures) is fetched at most every six hours and
  stored, so it is there right after a restart.

Errors of OpenLigaDB or ESPN surface as ``MatchdayApiError``; nothing here
touches the coordinator's own data.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import MatchdayApiError
from .api.espn import EspnClient, EspnEvent
from .api.openligadb import OldbMatch, OpenLigaDbClient, apply_crests
from .const import DOMAIN, LOGGER, Competition
from .logic import parse_utc
from .logic.matchday import LIVE_STATUSES, group_order_id
from .logic.merge import NORMALIZERS, SUMMARY_PLACEHOLDERS, merge_matches
from .logic.schedule import (
    build_matchdays,
    is_future,
    iso_utc,
    match_settled,
    matchday_settled,
    scoreboard_months_of,
    scoreboard_pso,
)
from .logic.summary import DETAIL_FIELDS, SUMMARY_STATUSES, parse_summary

STORAGE_VERSION = 1
SAVE_DELAY = 10

SCHEDULE_REFRESH = timedelta(hours=6)
SCHEDULE_RETRY = timedelta(minutes=30)
CACHE_TTL = timedelta(minutes=15)
LIVE_DETAILS_TTL = timedelta(minutes=1)
# Measured: a league matchday is ~30 KB in the store, the first cup round
# (32 matches) ~120 KB, the details of a match ~2 KB. The limits hold a full
# season of matchdays and match details: roughly 2 MB per competition at most.
MAX_STORED_MATCHDAYS = 40
MAX_STORED_DETAILS = 400
MAX_MEMORY_ENTRIES = 40


class UnknownMatchdayError(Exception):
    """The competition has no such matchday in the requested season."""


class UnknownMatchError(Exception):
    """OpenLigaDB knows no such match in this competition."""


@dataclass(frozen=True, slots=True)
class BrowsedMatchday:
    """A matchday in the ``matches`` format of the sensor."""

    season: int
    matchday: int
    name: str
    matches: list[dict[str, Any]]


def details_of(match: dict[str, Any]) -> dict[str, Any]:
    """Return the summary fields of a match (``matches`` format), copied."""
    return {
        key: copy.deepcopy(match.get(key, SUMMARY_PLACEHOLDERS[key]))
        for key in DETAIL_FIELDS
    }


def build_matchday(
    competition: Competition,
    season: int,
    matchday: int,
    oldb_matches: Sequence[OldbMatch],
    events: Sequence[EspnEvent],
    local_tz: tzinfo,
) -> BrowsedMatchday:
    """Merge a matchday like the coordinator does (no Home Assistant needed).

    The summary fields stay empty (``get_match_details`` fills them on request),
    except the penalty shootout, which the scoreboard already carries.
    """
    matches = merge_matches(
        oldb_matches,
        events,
        NORMALIZERS[competition.team_names],
        local_tz,
        oldb_results=competition.oldb_results,
    )
    shootouts = scoreboard_pso(events)
    for match in matches:
        if match.get("espn_id") in shootouts:
            match["pso"] = shootouts[match["espn_id"]]
    group = oldb_matches[0].get("group") if oldb_matches else None
    name = group.get("groupName") if isinstance(group, dict) else None
    return BrowsedMatchday(season, matchday, str(name or matchday), matches)


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"


def _empty_data() -> dict[str, Any]:
    return {"matchdays": {}, "details": {}, "schedule": None}


def _valid_data(data: Any) -> dict[str, Any]:
    """Return stored data with the expected structure; drop what does not fit."""
    result = _empty_data()
    if not isinstance(data, dict):
        return result
    matchdays = data.get("matchdays")
    if isinstance(matchdays, dict):
        result["matchdays"] = {
            key: value
            for key, value in matchdays.items()
            if isinstance(value, dict)
            and isinstance(value.get("season"), int)
            and isinstance(value.get("matchday"), int)
            and isinstance(value.get("name"), str)
            and isinstance(value.get("matches"), list)
        }
    details = data.get("details")
    if isinstance(details, dict):
        result["details"] = {
            key: value
            for key, value in details.items()
            if isinstance(value, dict)
            and isinstance(value.get("kickoff"), str)
            and isinstance(value.get("details"), dict)
        }
    schedule = data.get("schedule")
    if (
        isinstance(schedule, dict)
        and isinstance(schedule.get("season"), int)
        and isinstance(schedule.get("fetched"), str)
        and isinstance(schedule.get("matchdays"), list)
    ):
        result["schedule"] = schedule
    return result


class CachingArchive:
    """Memory cache and shared fetches behind the archives of a competition."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, competition: Competition
    ) -> None:
        """Initialize with an empty memory cache."""
        self._hass = hass
        self._entry = entry
        self._competition = competition
        self._memory: dict[tuple[Any, ...], tuple[datetime, Any]] = {}
        self._inflight: dict[tuple[Any, ...], asyncio.Task[Any]] = {}

    def _drop_memory(self) -> None:
        """Cancel running fetches and drop the memory cache."""
        for task in self._inflight.values():
            task.cancel()
        self._inflight.clear()
        self._memory.clear()

    # --- memory cache and shared fetches --------------------------------------

    def _cached(self, key: tuple[Any, ...]) -> Any:
        """Return a fresh memory-cached value, ``None`` if missing or expired."""
        hit = self._memory.get(key)
        if hit is None:
            return None
        if dt_util.utcnow() >= hit[0]:
            del self._memory[key]
            return None
        return hit[1]

    def _remember(self, key: tuple[Any, ...], value: Any, ttl: timedelta) -> None:
        now = dt_util.utcnow()
        for old in [k for k, (expires, _v) in self._memory.items() if now >= expires]:
            del self._memory[old]
        self._memory.pop(key, None)
        self._memory[key] = (now + ttl, value)
        while len(self._memory) > MAX_MEMORY_ENTRIES:
            del self._memory[next(iter(self._memory))]

    async def _shared[T](
        self,
        key: tuple[Any, ...],
        factory: Callable[[], Coroutine[Any, Any, T]],
    ) -> T:
        """Run ``factory`` once for all concurrent callers asking for ``key``.

        The fetch runs as a background task of the config entry, so a caller
        that gives up does not cancel it for the others, and unloading the
        entry cancels it.
        """
        task = self._inflight.get(key)
        if task is None:
            task = self._entry.async_create_background_task(
                self._hass,
                factory(),
                f"{DOMAIN} {self._competition.key} {key}",
            )
            if not task.done():
                self._inflight[key] = task
                task.add_done_callback(partial(self._forget, key))
        return await asyncio.shield(task)

    def _forget(self, key: tuple[Any, ...], task: asyncio.Task[Any]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            # The callers got it through the shield; do not log it as unhandled.
            task.exception()


class MatchdayArchive(CachingArchive):
    """Fetch and cache matchdays and match details of one competition."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        competition: Competition,
        oldb: OpenLigaDbClient,
        espn: EspnClient,
    ) -> None:
        """Initialize; call ``async_load`` before use."""
        if competition.openligadb_shortcut is None:
            raise ValueError(f"{competition.key} has no OpenLigaDB source")
        super().__init__(hass, entry, competition)
        self._league = competition.openligadb_shortcut
        self._oldb = oldb
        self._espn = espn
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, _storage_key(entry.entry_id)
        )
        self._data = _empty_data()
        self._dirty = False
        self._schedule_retry_at: datetime | None = None

    # --- lifecycle ------------------------------------------------------------

    async def async_load(self) -> None:
        """Load the stored matchdays, details and schedule."""
        self._data = _valid_data(await self._store.async_load())

    async def async_unload(self) -> None:
        """Cancel running fetches, write pending changes, drop the memory cache."""
        self._drop_memory()
        if self._dirty:
            self._dirty = False
            await self._store.async_save(self._data)

    @staticmethod
    async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
        """Delete the stored data of a removed config entry."""
        await Store[dict[str, Any]](
            hass, STORAGE_VERSION, _storage_key(entry_id)
        ).async_remove()

    def _save(self) -> None:
        self._dirty = True
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    def _data_to_save(self) -> dict[str, Any]:
        self._dirty = False
        return self._data

    # --- season schedule ------------------------------------------------------

    def matchdays(self, season: int) -> list[dict[str, Any]]:
        """Return the matchdays of ``season`` (empty until known)."""
        schedule = self._data["schedule"]
        if schedule is None or schedule["season"] != season:
            return []
        return schedule["matchdays"]

    async def async_refresh_schedule(self, season: int, now: datetime) -> None:
        """Fetch the season schedule when due; failures keep the old one."""
        schedule = self._data["schedule"]
        fetched = parse_utc(schedule["fetched"]) if schedule else None
        if (
            schedule is not None
            and schedule["season"] == season
            and fetched is not None
            and now - fetched < SCHEDULE_REFRESH
        ):
            return
        if self._schedule_retry_at is not None and now < self._schedule_retry_at:
            return
        try:
            groups = await self._oldb.async_get_available_groups(self._league, season)
            season_matches = await self._oldb.async_get_season_matches(
                self._league, season
            )
        except MatchdayApiError as err:
            LOGGER.debug("%s: season schedule unavailable: %s", self._league, err)
            self._schedule_retry_at = now + SCHEDULE_RETRY
            return
        matchdays = build_matchdays(groups, season_matches)
        if not matchdays:
            LOGGER.debug("%s: season %s has no matchdays yet", self._league, season)
            self._schedule_retry_at = now + SCHEDULE_RETRY
            return
        self._schedule_retry_at = None
        self._data["schedule"] = {
            "season": season,
            "fetched": iso_utc(now),
            "matchdays": matchdays,
        }
        self._save()

    async def _async_matchday_name(self, season: int, matchday: int) -> str | None:
        """Return the name of a matchday without fixtures, ``None`` if unknown."""
        if season == (self._data["schedule"] or {}).get("season"):
            groups: list[dict[str, Any]] = [
                {"groupOrderID": item["matchday"], "groupName": item["name"]}
                for item in self._data["schedule"]["matchdays"]
            ]
        else:
            key = ("groups", season)
            groups = self._cached(key)
            if groups is None:
                groups = await self._oldb.async_get_available_groups(
                    self._league, season
                )
                self._remember(key, groups, SCHEDULE_REFRESH)
        return next(
            (
                str(group.get("groupName") or matchday)
                for group in groups
                if group.get("groupOrderID") == matchday
            ),
            None,
        )

    # --- matchdays ------------------------------------------------------------

    async def async_get_matchday(self, season: int, matchday: int) -> BrowsedMatchday:
        """Return a matchday in the ``matches`` format (a copy, safe to change).

        Raises:
            UnknownMatchdayError: if the season has no such matchday.
            MatchdayApiError: if OpenLigaDB or ESPN fail.
        """
        stored = self._data["matchdays"].get(f"{season}/{matchday}")
        if stored is not None:
            result = BrowsedMatchday(
                season, matchday, stored["name"], stored["matches"]
            )
        else:
            key = ("matchday", season, matchday)
            result = self._cached(key) or await self._shared(
                key, partial(self._async_fetch_matchday, season, matchday)
            )
        # Stored matchdays may carry crest URLs from before CRESTS changed.
        return BrowsedMatchday(
            result.season,
            result.matchday,
            result.name,
            [apply_crests(match) for match in copy.deepcopy(result.matches)],
        )

    async def _async_fetch_matchday(
        self, season: int, matchday: int
    ) -> BrowsedMatchday:
        key = ("matchday", season, matchday)
        oldb_matches = await self._oldb.async_get_match_data(
            self._league, season, matchday
        )
        if not oldb_matches:
            name = await self._async_matchday_name(season, matchday)
            if name is None:
                raise UnknownMatchdayError
            empty = BrowsedMatchday(season, matchday, name, [])
            self._remember(key, empty, CACHE_TTL)
            return empty

        now = dt_util.utcnow()
        events: list[EspnEvent] = []
        if not is_future(oldb_matches, now):
            events = await self._async_events(scoreboard_months_of(oldb_matches))
        result = build_matchday(
            self._competition,
            season,
            matchday,
            oldb_matches,
            events,
            dt_util.get_default_time_zone(),
        )
        if matchday_settled(result.matches, now):
            self._store_matchday(result)
        else:
            self._remember(key, result, CACHE_TTL)
        return result

    def _store_matchday(self, result: BrowsedMatchday) -> None:
        stored = self._data["matchdays"]
        stored[f"{result.season}/{result.matchday}"] = {
            "season": result.season,
            "matchday": result.matchday,
            "name": result.name,
            "matches": result.matches,
        }
        while len(stored) > MAX_STORED_MATCHDAYS:
            oldest = min(
                stored, key=lambda k: (stored[k]["season"], stored[k]["matchday"])
            )
            del stored[oldest]
        self._save()

    async def _async_events(self, months: list[str]) -> list[EspnEvent]:
        """Return the ESPN events of several months, de-duplicated by id."""
        events: dict[str, EspnEvent] = {}
        for month in months:
            key = ("scoreboard", month)
            scoreboard = self._cached(key)
            if scoreboard is None:
                scoreboard = await self._shared(
                    key, partial(self._async_fetch_scoreboard, month)
                )
            for event in scoreboard:
                events.setdefault(str(event["id"]), event)
        return list(events.values())

    async def _async_fetch_scoreboard(self, month: str) -> list[EspnEvent]:
        events = await self._espn.async_get_scoreboard(
            self._competition.espn_slug, month
        )
        self._remember(("scoreboard", month), events, CACHE_TTL)
        return events

    # --- match details --------------------------------------------------------

    async def async_get_match_details(self, match_id: int) -> dict[str, Any]:
        """Return the summary fields of a match that is not on the shown matchday.

        Raises:
            UnknownMatchError: if the competition has no such match.
            MatchdayApiError: if OpenLigaDB or ESPN fail.
        """
        stored = self._data["details"].get(str(match_id))
        if stored is not None:
            return copy.deepcopy(stored["details"])
        key = ("details", match_id)
        details = self._cached(key) or await self._shared(
            key, partial(self._async_fetch_details, match_id)
        )
        return copy.deepcopy(details)

    async def _async_fetch_details(self, match_id: int) -> dict[str, Any]:
        match = self._find_match(match_id) or await self._async_locate_match(match_id)
        details = details_of(match)
        espn_id = match.get("espn_id")
        fetched = False
        if espn_id and match.get("status") in SUMMARY_STATUSES:
            summary = await self._espn.async_get_summary(
                self._competition.espn_slug, str(espn_id)
            )
            for field, value in parse_summary(
                summary, match.get("espn_home_team_id")
            ).items():
                if value is not None:
                    details[field] = value
            fetched = True

        if fetched and match_settled(match, dt_util.utcnow()):
            self._store_details(match_id, match, details)
        else:
            live = match.get("status") in LIVE_STATUSES
            self._remember(
                ("details", match_id),
                details,
                LIVE_DETAILS_TTL if live else CACHE_TTL,
            )
        return details

    def _find_match(self, match_id: int) -> dict[str, Any] | None:
        """Return a match of a stored or memory-cached matchday."""
        candidates: list[list[dict[str, Any]]] = [
            item["matches"] for item in self._data["matchdays"].values()
        ]
        now = dt_util.utcnow()
        candidates.extend(
            value.matches
            for expires, value in self._memory.values()
            if isinstance(value, BrowsedMatchday) and now < expires
        )
        for matches in candidates:
            for match in matches:
                if match.get("matchID") == match_id:
                    return match
        return None

    async def _async_locate_match(self, match_id: int) -> dict[str, Any]:
        """Look a match up at OpenLigaDB and load its matchday."""
        oldb_match = await self._oldb.async_get_match(match_id)
        if (
            oldb_match is None
            or str(oldb_match.get("leagueShortcut") or "").lower()
            != self._league.lower()
        ):
            raise UnknownMatchError
        try:
            season = int(oldb_match.get("leagueSeason") or 0)
        except (TypeError, ValueError):
            season = 0
        number = group_order_id(oldb_match)
        if season <= 0 or number <= 0:
            raise UnknownMatchError
        try:
            browsed = await self.async_get_matchday(season, number)
        except UnknownMatchdayError as err:
            raise UnknownMatchError from err
        for match in browsed.matches:
            if match.get("matchID") == match_id:
                return match
        raise UnknownMatchError

    def _store_details(
        self, match_id: int, match: dict[str, Any], details: dict[str, Any]
    ) -> None:
        stored = self._data["details"]
        stored[str(match_id)] = {
            "kickoff": str(match.get("matchDateTimeUTC") or ""),
            "details": details,
        }
        while len(stored) > MAX_STORED_DETAILS:
            del stored[min(stored, key=lambda k: stored[k]["kickoff"])]
        self._save()

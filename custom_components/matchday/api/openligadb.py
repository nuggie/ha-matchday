"""Async client for the public OpenLigaDB read API (https://api.openligadb.de)."""

from __future__ import annotations

from typing import Any

import aiohttp

from ..crests import CRESTS
from . import MatchdayApiError, MatchdayNotFoundError, async_get_json

BASE_URL = "https://api.openligadb.de"

type OldbMatch = dict[str, Any]


class OpenLigaDbClient:
    """Fetch fixtures and matchday groupings from OpenLigaDB."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize with a shared aiohttp session."""
        self._session = session

    async def async_get_last_match(self, league: str) -> OldbMatch | None:
        """Return the most recent match by kickoff time, or ``None`` if unknown."""
        return _as_match(
            await async_get_json(
                self._session, f"{BASE_URL}/getlastmatchbyleagueshortcut/{league}"
            )
        )

    async def async_get_next_match(self, league: str) -> OldbMatch | None:
        """Return the next match by kickoff time, or ``None`` if unknown."""
        return _as_match(
            await async_get_json(
                self._session, f"{BASE_URL}/getnextmatchbyleagueshortcut/{league}"
            )
        )

    async def async_get_match_data(
        self, league: str, season: int, group_order_id: int
    ) -> list[OldbMatch]:
        """Return all matches of one matchday (``group_order_id``) of a season.

        Raises:
            MatchdayApiError: if the answer is not a list of match objects.
        """
        data = await async_get_json(
            self._session,
            f"{BASE_URL}/getmatchdata/{league}/{season}/{group_order_id}",
        )
        if not isinstance(data, list):
            raise MatchdayApiError(
                f"getmatchdata/{league}/{season}/{group_order_id} did not return a list"
            )
        return [match for match in data if _as_match(match) is not None]

    async def async_get_season_matches(
        self, league: str, season: int
    ) -> list[OldbMatch]:
        """Return all matches of a season (all matchdays, several hundred KB).

        Raises:
            MatchdayApiError: if the answer is not a list.
        """
        data = await async_get_json(
            self._session, f"{BASE_URL}/getmatchdata/{league}/{season}"
        )
        if not isinstance(data, list):
            raise MatchdayApiError(
                f"getmatchdata/{league}/{season} did not return a list"
            )
        return [match for match in data if _as_match(match) is not None]

    async def async_get_match(self, match_id: int) -> OldbMatch | None:
        """Return one match by its OpenLigaDB id, ``None`` if there is no such match.

        Raises:
            MatchdayApiError: on errors other than "not found".
        """
        try:
            data = await async_get_json(
                self._session, f"{BASE_URL}/getmatchdata/{match_id}"
            )
        except MatchdayNotFoundError:
            return None
        return _as_match(data)

    async def async_get_available_groups(
        self, league: str, season: int
    ) -> list[dict[str, Any]]:
        """Return the matchdays/rounds of a season (name, order id), also future ones.

        Raises:
            MatchdayApiError: if the answer is not a list.
        """
        data = await async_get_json(
            self._session, f"{BASE_URL}/getavailablegroups/{league}/{season}"
        )
        if not isinstance(data, list):
            raise MatchdayApiError(
                f"getavailablegroups/{league}/{season} did not return a list"
            )
        return [
            group
            for group in data
            if isinstance(group, dict)
            and isinstance(group.get("groupOrderID"), int)
            and not isinstance(group.get("groupOrderID"), bool)
        ]

    async def async_get_table(self, league: str, season: int) -> list[dict[str, Any]]:
        """Return the league table (only its team names and crests are used).

        Raises:
            MatchdayApiError: if the answer is not a list.
        """
        data = await async_get_json(
            self._session, f"{BASE_URL}/getbltable/{league}/{season}"
        )
        if not isinstance(data, list):
            raise MatchdayApiError(
                f"getbltable/{league}/{season} did not return a list"
            )
        return [
            _apply_crest(team, "teamInfoId")
            for team in data
            if isinstance(team, dict) and isinstance(team.get("teamName"), str)
        ]


def _apply_crest(team: Any, id_key: str = "teamId") -> Any:
    """Set the crest of ``team`` from CRESTS in place, if it has an entry."""
    if isinstance(team, dict) and (url := CRESTS.get(team.get(id_key))):
        team["teamIconUrl"] = url
    return team


def apply_crests(match: OldbMatch) -> OldbMatch:
    """Set the crests of both teams of ``match`` from CRESTS in place."""
    _apply_crest(match.get("team1"))
    _apply_crest(match.get("team2"))
    return match


def _as_match(data: Any) -> OldbMatch | None:
    """Return ``data`` if it looks like an OpenLigaDB match object."""
    if (
        isinstance(data, dict)
        and isinstance(data.get("matchDateTimeUTC"), str)
        and isinstance(data.get("group"), dict)
    ):
        return apply_crests(data)
    return None

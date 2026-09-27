"""Async client for the public OpenLigaDB read API (https://api.openligadb.de)."""

from __future__ import annotations

from typing import Any

import aiohttp

from . import MatchdayApiError, async_get_json

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
            team
            for team in data
            if isinstance(team, dict) and isinstance(team.get("teamName"), str)
        ]


def _as_match(data: Any) -> OldbMatch | None:
    """Return ``data`` if it looks like an OpenLigaDB match object."""
    if (
        isinstance(data, dict)
        and isinstance(data.get("matchDateTimeUTC"), str)
        and isinstance(data.get("group"), dict)
    ):
        return data
    return None

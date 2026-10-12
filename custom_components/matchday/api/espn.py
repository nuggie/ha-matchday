"""Async client for the (unofficial) ESPN soccer API: scoreboard, summary, standings."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

import aiohttp

from . import MatchdayApiError, async_get_json

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
# Standings live under a different path ("apis/v2", not "apis/site/v2").
STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/soccer"

type EspnEvent = dict[str, Any]

# Without ``limit`` ESPN cuts a scoreboard at 100 events: the July 2026 scoreboard
# of the Conference League qualifying has 150 (two rounds, the second legs were
# missing). 500 covers any month of any competition here.
SCOREBOARD_LIMIT = "500"


def scoreboard_months(first: date, last: date) -> list[str]:
    """Return the ``YYYYMM`` values needed to cover ``first`` .. ``last``.

    ESPN dropped the range syntax ``?dates=YYYYMMDD-YYYYMMDD`` in September 2026
    (HTTP 400); only whole months work. Every month from the first to the last
    date is listed: a matchday spans one or two, a season or a cup stage more.
    """
    months: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(f"{year}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


class EspnClient:
    """Fetch scoreboards from ESPN."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize with a shared aiohttp session."""
        self._session = session

    async def async_get_scoreboard(self, slug: str, month: str) -> list[EspnEvent]:
        """Return all events of league ``slug`` in ``month`` (``YYYYMM``).

        Raises:
            MatchdayApiError: if the answer has no ``events`` list.
        """
        data = await async_get_json(
            self._session,
            f"{BASE_URL}/{slug}/scoreboard",
            params={"dates": month, "limit": SCOREBOARD_LIMIT},
        )
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            raise MatchdayApiError(f"ESPN scoreboard {slug}/{month} has no events list")
        return [event for event in events if _is_event(event)]

    async def async_get_events(
        self, slug: str, months: Iterable[str]
    ) -> list[EspnEvent]:
        """Return the events of several months, de-duplicated by event id.

        Month queries also return edge games of neighbouring months, so the same
        event can show up twice. Any failing month fails the whole call: a missing
        month would look like "these games do not exist".
        """
        events: dict[str, EspnEvent] = {}
        for month in months:
            for event in await self.async_get_scoreboard(slug, month):
                events.setdefault(str(event["id"]), event)
        return list(events.values())

    async def async_get_summary(self, slug: str, event_id: str) -> dict[str, Any]:
        """Return the match summary (key events, box score, rosters) of an event.

        Raises:
            MatchdayApiError: if the answer is not a summary object.
        """
        data = await async_get_json(
            self._session, f"{BASE_URL}/{slug}/summary", params={"event": event_id}
        )
        if not isinstance(data, dict) or not isinstance(data.get("header"), dict):
            raise MatchdayApiError(f"ESPN summary {slug}/{event_id} has no header")
        return data

    async def async_get_standings(self, slug: str, season: int) -> list[dict[str, Any]]:
        """Return the standings entries of league ``slug`` in ``season``, by rank.

        Raises:
            MatchdayApiError: if the answer has no (non-empty) entries list.
        """
        data = await async_get_json(
            self._session,
            f"{STANDINGS_URL}/{slug}/standings",
            params={"season": str(season)},
        )
        children = data.get("children") if isinstance(data, dict) else None
        first = children[0] if isinstance(children, list) and children else None
        standings = first.get("standings") if isinstance(first, dict) else None
        entries = standings.get("entries") if isinstance(standings, dict) else None
        if not isinstance(entries, list) or not entries:
            raise MatchdayApiError(f"ESPN standings {slug}/{season} have no entries")
        return [entry for entry in entries if _is_standings_entry(entry)]


def _is_event(event: Any) -> bool:
    """Return True for events that carry what the merge needs."""
    if not isinstance(event, dict) or "id" not in event:
        return False
    competitions = event.get("competitions")
    return (
        isinstance(competitions, list)
        and bool(competitions)
        and isinstance(competitions[0], dict)
        and isinstance(competitions[0].get("date"), str)
        and isinstance(competitions[0].get("competitors"), list)
    )


def _is_standings_entry(entry: Any) -> bool:
    """Return True for standings entries with a team id and a stats list."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("team"), dict)
        and "id" in entry["team"]
        and isinstance(entry.get("stats"), list)
    )

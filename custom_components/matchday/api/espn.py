"""Async client for the (unofficial) ESPN soccer scoreboard API."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

import aiohttp

from . import MatchdayApiError, async_get_json

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

type EspnEvent = dict[str, Any]


def scoreboard_months(first: date, last: date) -> list[str]:
    """Return the ``YYYYMM`` values needed to cover ``first`` .. ``last``.

    ESPN dropped the range syntax ``?dates=YYYYMMDD-YYYYMMDD`` in September 2026
    (HTTP 400); only whole months work. A matchday spans a few days, so this is
    the month of the first date plus, across a month boundary, that of the last.
    """
    months = [first.strftime("%Y%m")]
    if (last_month := last.strftime("%Y%m")) != months[0]:
        months.append(last_month)
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
            self._session, f"{BASE_URL}/{slug}/scoreboard", params={"dates": month}
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

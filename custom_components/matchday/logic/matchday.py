"""Which matchday to show, and how often to poll.

Mirrors the ``oldb_pick`` / ``should_call_espn`` logic of the Jinja template sensors
this integration replaces (templates.yaml, state 2026-09-26).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from . import parse_utc

LIVE_STATUSES = frozenset({"live", "ht"})

INTERVAL_LIVE = timedelta(seconds=60)
INTERVAL_WINDOW = timedelta(minutes=5)
INTERVAL_IDLE = timedelta(minutes=30)

# A match counts as "hot" (fast polling) from 10 minutes before kickoff. A match
# still marked "pre" stays hot for a while after its kickoff time: ESPN may not
# have switched it to "in" yet, or it is not matched at all.
KICKOFF_LEAD = timedelta(minutes=10)
KICKOFF_GRACE = timedelta(minutes=130)


@dataclass(frozen=True, slots=True)
class MatchdayPick:
    """A matchday of a season, as OpenLigaDB numbers it."""

    group_order_id: int
    season: int


def default_season(now: datetime) -> int:
    """Return the season a date most likely belongs to (seasons start in July)."""
    return now.year if now.month >= 7 else now.year - 1


def _group_order_id(match: Mapping[str, Any] | None) -> int:
    """Return the matchday number of an OpenLigaDB match, 0 if unknown."""
    if not match:
        return 0
    group = match.get("group")
    if not isinstance(group, Mapping):
        return 0
    try:
        return int(group.get("groupOrderID") or 0)
    except (TypeError, ValueError):
        return 0


def _season(match: Mapping[str, Any], fallback: int) -> int:
    """Return ``leagueSeason`` of a match, ``fallback`` if missing."""
    try:
        return int(match.get("leagueSeason") or fallback)
    except (TypeError, ValueError):
        return fallback


def pick_matchday(
    last_match: Mapping[str, Any] | None,
    next_match: Mapping[str, Any] | None,
    now: datetime,
    previous: MatchdayPick | None = None,
) -> MatchdayPick:
    """Choose the matchday to show from the last and the next match by kickoff.

    The matchday closer in time wins (``now - last_kickoff <= next_kickoff - now``
    keeps the last one), so the display switches in the middle of a break. Missing
    one side (start or end of the season, API error) uses the other one; missing
    both keeps ``previous`` (or matchday 1).
    """
    fallback_season = previous.season if previous else default_season(now)
    last_kickoff = parse_utc(last_match.get("matchDateTimeUTC")) if last_match else None
    next_kickoff = parse_utc(next_match.get("matchDateTimeUTC")) if next_match else None
    last_group = _group_order_id(last_match) if last_kickoff else 0
    next_group = _group_order_id(next_match) if next_kickoff else 0

    source: Mapping[str, Any] | None = None
    if last_group and next_group and last_kickoff and next_kickoff:
        source = last_match if now - last_kickoff <= next_kickoff - now else next_match
    elif last_group:
        source = last_match
    elif next_group:
        source = next_match

    if source is None:
        if previous is not None:
            return previous
        return MatchdayPick(1, fallback_season)
    return MatchdayPick(_group_order_id(source), _season(source, default_season(now)))


def round_window(matches: Iterable[Mapping[str, Any]]) -> tuple[date, date] | None:
    """Return the first and last (UTC) match date of a matchday."""
    dates = sorted(
        kickoff.date()
        for match in matches
        if (kickoff := parse_utc(match.get("matchDateTimeUTC"))) is not None
    )
    if not dates:
        return None
    return dates[0], dates[-1]


def in_espn_window(window: tuple[date, date], today: date) -> bool:
    """Return True from the day before the first to two days after the last match."""
    first, last = window
    return first - timedelta(days=1) <= today <= last + timedelta(days=2)


def round_past(window: tuple[date, date], today: date) -> bool:
    """Return True once the last match date of the shown matchday is over."""
    return today > window[1]


def should_query_espn(window: tuple[date, date], today: date) -> bool:
    """Return True if ESPN live data is needed for the shown matchday.

    Besides the window around the matchday, ESPN stays queried while a finished
    matchday is still shown (``round_past``) - otherwise scorers and cards would
    disappear two days after the matchday although it is still on display.
    """
    return in_espn_window(window, today) or round_past(window, today)


def any_live(matches: Iterable[Mapping[str, Any]]) -> bool:
    """Return True if a match is currently running (live or half time)."""
    return any(match.get("status") in LIVE_STATUSES for match in matches)


def _is_hot(match: Mapping[str, Any], now: datetime) -> bool:
    """Return True if a match is running or about to start."""
    status = match.get("status")
    if status in LIVE_STATUSES:
        return True
    if status != "pre":
        return False
    kickoff = parse_utc(match.get("matchDateTimeUTC"))
    return kickoff is not None and (
        now - KICKOFF_GRACE <= kickoff <= now + KICKOFF_LEAD
    )


def update_interval(
    matches: Iterable[Mapping[str, Any]],
    window: tuple[date, date] | None,
    today: date,
    now: datetime,
) -> timedelta:
    """Return the polling interval: fast while live, medium around a matchday."""
    matches = list(matches)
    if any(_is_hot(match, now) for match in matches):
        return INTERVAL_LIVE
    if window is not None and in_espn_window(window, today):
        return INTERVAL_WINDOW
    return INTERVAL_IDLE

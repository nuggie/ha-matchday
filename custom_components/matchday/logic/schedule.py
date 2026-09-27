"""The matchdays of a season, and when a browsed matchday or match is settled.

Pure helpers behind the ``matchdays`` attribute and the ``get_matchday`` /
``get_match_details`` actions (``archive.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from . import parse_utc
from .matchday import group_order_id
from .summary import pso_from_competitors

# A matchday whose first kickoff is further away than this is "future": its
# matches come from OpenLigaDB alone, ESPN is not asked.
FUTURE_AFTER = timedelta(days=1)
# A match (and a matchday whose matches all are) is settled - its data will not
# change any more and may be stored for good - this long after its kickoff.
SETTLED_AFTER = timedelta(days=1)


def iso_utc(value: datetime) -> str:
    """Return ``value`` as ``2026-09-18T18:30:00Z`` (OpenLigaDB's UTC format)."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_matchdays(
    groups: Iterable[Mapping[str, Any]],
    season_matches: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return the matchdays of a season with the kickoff range of each, by number.

    ``groups`` (``getavailablegroups``) names every matchday or round, also those
    without fixtures yet (later cup rounds): their kickoffs are ``None``. Groups
    that only show up in the fixtures are listed with the name found there.
    """
    names: dict[int, str] = {}
    for group in groups:
        number = group.get("groupOrderID")
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            names[number] = str(group.get("groupName") or number)

    kickoffs: dict[int, list[datetime]] = {}
    for match in season_matches:
        number = group_order_id(match)
        kickoff = parse_utc(match.get("matchDateTimeUTC"))
        if number <= 0 or kickoff is None:
            continue
        kickoffs.setdefault(number, []).append(kickoff)
        if number not in names:
            group = match.get("group")
            name = group.get("groupName") if isinstance(group, Mapping) else None
            names[number] = str(name or number)

    return [
        {
            "matchday": number,
            "name": names[number],
            "first_kickoff": iso_utc(min(kickoffs[number]))
            if number in kickoffs
            else None,
            "last_kickoff": iso_utc(max(kickoffs[number]))
            if number in kickoffs
            else None,
        }
        for number in sorted(names)
    ]


def _kickoffs(matches: Iterable[Mapping[str, Any]]) -> list[datetime]:
    return sorted(
        kickoff
        for match in matches
        if (kickoff := parse_utc(match.get("matchDateTimeUTC"))) is not None
    )


def is_future(matches: Sequence[Mapping[str, Any]], now: datetime) -> bool:
    """Return True if the first kickoff is more than a day away (or unknown)."""
    kickoffs = _kickoffs(matches)
    return not kickoffs or kickoffs[0] > now + FUTURE_AFTER


def scoreboard_months_of(matches: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return the ``YYYYMM`` months of all kickoffs, in order.

    For a regular matchday this is the month of the first and, across a month
    boundary, of the last kickoff, like the coordinator asks. A matchday with a
    match rescheduled months later gets exactly the months its matches are in.
    """
    return sorted({kickoff.strftime("%Y%m") for kickoff in _kickoffs(matches)})


def match_settled(match: Mapping[str, Any], now: datetime) -> bool:
    """Return True for a finished match that kicked off more than a day ago."""
    kickoff = parse_utc(match.get("matchDateTimeUTC"))
    return (
        match.get("status") == "post"
        and kickoff is not None
        and now - kickoff >= SETTLED_AFTER
    )


def matchday_settled(matches: Sequence[Mapping[str, Any]], now: datetime) -> bool:
    """Return True if a matchday will not change any more.

    Every match finished, paired with ESPN (an unpaired match lacks score and
    scorers; it is asked again next time) and kicked off more than a day ago.
    """
    return bool(matches) and all(
        match.get("espn_id") and match_settled(match, now) for match in matches
    )


def scoreboard_pso(events: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the penalty shootouts of scoreboard events, by ESPN event id."""
    shootouts: dict[str, dict[str, Any]] = {}
    for event in events:
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions else {}
        competitors = competition.get("competitors") or []
        home = next(
            (
                c
                for c in competitors
                if isinstance(c, Mapping) and c.get("homeAway") == "home"
            ),
            None,
        )
        away = next(
            (
                c
                for c in competitors
                if isinstance(c, Mapping) and c.get("homeAway") == "away"
            ),
            None,
        )
        if home is None or away is None:
            continue
        if (pso := pso_from_competitors(home, away)) is not None:
            shootouts[str(event.get("id"))] = pso
    return shootouts

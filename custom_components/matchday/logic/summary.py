"""Match details from ESPN summaries, and when to fetch them.

The scoreboard carries status, score, scorers and cards. Substitutions, half-time
score, statistics, lineups and the penalty shootout come from the per-match
summary (``.../soccer/<slug>/summary?event=<id>``). Parsing mirrors the summary
loop of the Jinja template sensors (templates.yaml, state 2026-09-27); the
fetch policy is stricter, see ``SummaryTracker``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import parse_utc

# Summary fields in the ``matches`` format; ``None`` in a parse result means
# "the summary did not say", and the last known value stays.
DETAIL_FIELDS = ("substitutions", "ht_score", "stats", "lineups", "pso")

# ESPN statistic names -> keys of the ``stats`` attribute ([home, away] ints).
STAT_KEYS: dict[str, str] = {
    "possession": "possessionPct",
    "shots": "totalShots",
    "shots_on": "shotsOnTarget",
    "corners": "wonCorners",
}

SUMMARY_STATUSES = frozenset({"live", "ht", "post"})

# While a match runs its summary is fetched every three minutes (as in Jinja).
# Refreshes arrive every 60 s with some jitter; the slack keeps "three minutes"
# from sliding to four.
SUMMARY_REFRESH = timedelta(minutes=3)
_REFRESH_SLACK = timedelta(seconds=15)
# A finished match is fetched once more after this time, so late corrections of
# the final statistics are picked up; after that it is never fetched again.
SUMMARY_SETTLE = timedelta(minutes=2)
# First seen finished this long after kickoff (restart, late setup): ESPN has
# long settled, one fetch is final.
SETTLED_AFTER_KICKOFF = timedelta(hours=3)
# A finished match whose summary keeps failing is given up after this many
# attempts (three minutes apart at least) instead of being asked forever.
MAX_POST_FAILURES = 3


def _by_side(items: Iterable[Any], side: str) -> Mapping[str, Any] | None:
    """Return the first mapping with ``homeAway == side``."""
    for item in items:
        if isinstance(item, Mapping) and item.get("homeAway") == side:
            return item
    return None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _stat_number(value: Any) -> int:
    """Jinja's ``value | float(0) | round | int`` (round half to even)."""
    try:
        return round(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def parse_substitutions(
    summary: Mapping[str, Any], home_team_id: Any
) -> list[dict[str, Any]]:
    """Return the substitutions from ``keyEvents`` in ESPN order.

    ``participants[0]`` comes on, ``participants[1]`` goes off.
    """
    substitutions: list[dict[str, Any]] = []
    for event in _list(summary.get("keyEvents")):
        if not isinstance(event, Mapping):
            continue
        participants = _list(event.get("participants"))
        if _mapping(event.get("type")).get("type") != "substitution" or (
            len(participants) < 2
        ):
            continue
        clock = _mapping(event.get("clock"))
        substitutions.append(
            {
                "in": _mapping(_mapping(participants[0]).get("athlete")).get(
                    "displayName", ""
                ),
                "out": _mapping(_mapping(participants[1]).get("athlete")).get(
                    "displayName", ""
                ),
                "minute": clock.get("displayValue", ""),
                "sortKey": clock.get("value", 0),
                "team": "team1"
                if _mapping(event.get("team")).get("id", "") == home_team_id
                else "team2",
            }
        )
    return substitutions


def _header_sides(
    summary: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    competitions = _list(_mapping(summary.get("header")).get("competitions"))
    if not competitions:
        return None
    competitors = _list(_mapping(competitions[0]).get("competitors"))
    home, away = _by_side(competitors, "home"), _by_side(competitors, "away")
    if home is None or away is None:
        return None
    return home, away


def parse_ht_score(summary: Mapping[str, Any]) -> str | None:
    """Return "h:a" from the first line score of both teams.

    Like Jinja this is the running first-half score until half time.
    """
    sides = _header_sides(summary)
    if sides is None:
        return None
    home = _list(sides[0].get("linescores"))
    away = _list(sides[1].get("linescores"))
    if not home or not away:
        return None
    return (
        f"{_mapping(home[0]).get('displayValue', '')}:"
        f"{_mapping(away[0]).get('displayValue', '')}"
    )


def parse_stats(summary: Mapping[str, Any]) -> dict[str, list[int]] | None:
    """Return possession, shots, shots on target and corners as [home, away]."""
    teams = _list(_mapping(summary.get("boxscore")).get("teams"))
    home, away = _by_side(teams, "home"), _by_side(teams, "away")
    home_stats = _list(home.get("statistics")) if home else []
    away_stats = _list(away.get("statistics")) if away else []
    if not home_stats or not away_stats:
        return None

    def find(stats: Sequence[Any], name: str) -> Mapping[str, Any] | None:
        return next(
            (s for s in stats if isinstance(s, Mapping) and s.get("name") == name),
            None,
        )

    stats: dict[str, list[int]] = {}
    for key, name in STAT_KEYS.items():
        home_stat, away_stat = find(home_stats, name), find(away_stats, name)
        if home_stat is not None and away_stat is not None:
            stats[key] = [
                _stat_number(home_stat.get("displayValue")),
                _stat_number(away_stat.get("displayValue")),
            ]
    return stats or None


def parse_lineups(summary: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return formation and starting eleven of both teams from ``rosters``."""
    rosters = _list(summary.get("rosters"))
    home, away = _by_side(rosters, "home"), _by_side(rosters, "away")
    if home is None or away is None:
        return None

    def starters(roster: Mapping[str, Any]) -> list[str]:
        return [
            _mapping(player.get("athlete")).get("displayName", "")
            if "athlete" in player
            else ""
            for player in _list(roster.get("roster"))
            if isinstance(player, Mapping) and player.get("starter") is True
        ]

    home_players, away_players = starters(home), starters(away)
    if not home_players or not away_players:
        return None
    return {
        "home": {"formation": home.get("formation", ""), "players": home_players},
        "away": {"formation": away.get("formation", ""), "players": away_players},
    }


def parse_pso(summary: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the penalty shootout (``shootoutScore`` of both teams), if any."""
    sides = _header_sides(summary)
    if sides is None:
        return None
    home, away = sides
    if home.get("shootoutScore") is None or away.get("shootoutScore") is None:
        return None
    winner = "team1" if home.get("winner") else "team2" if away.get("winner") else None
    return {
        "team1": home["shootoutScore"],
        "team2": away["shootoutScore"],
        "winner": winner,
    }


def parse_summary(summary: Mapping[str, Any], home_team_id: Any) -> dict[str, Any]:
    """Return the detail fields of a summary; ``None`` values mean "unknown".

    ``substitutions`` is always a list: a summary without substitutions means
    there were none yet.
    """
    return {
        "substitutions": parse_substitutions(summary, home_team_id),
        "ht_score": parse_ht_score(summary),
        "stats": parse_stats(summary),
        "lineups": parse_lineups(summary),
        "pso": parse_pso(summary),
    }


@dataclass(slots=True)
class SummaryEntry:
    """What is known about the summary of one match."""

    espn_id: str
    details: dict[str, Any] = field(default_factory=dict)
    fetched_at: datetime | None = None
    fetched_status: str | None = None
    post_since: datetime | None = None
    final: bool = False
    failures: int = 0


class SummaryTracker:
    """Remember match details per OpenLigaDB match and decide what to fetch.

    - Only matches with an ESPN id that are live, at half time or finished.
    - Running matches: on every status change and every three minutes.
    - Finished matches: on the final whistle, once more after ``SUMMARY_SETTLE``,
      then never again (``final``). A match first seen finished long after its
      kickoff (restart) is fetched exactly once. Failing fetches are retried
      every three minutes, for finished matches ``MAX_POST_FAILURES`` times.
    - Matches that are not fetched keep their last details (Jinja's ``prev``
      fallback), also after the matchday window closed.
    """

    def __init__(self) -> None:
        """Start without any details."""
        self._entries: dict[Any, SummaryEntry] = {}

    def entry(self, match_id: Any) -> SummaryEntry | None:
        """Return the entry of an OpenLigaDB match id (tests, diagnostics)."""
        return self._entries.get(match_id)

    def due(
        self, matches: Sequence[Mapping[str, Any]], now: datetime
    ) -> list[Mapping[str, Any]]:
        """Return the matches whose summary should be fetched now.

        Also forgets matches that left the list and restarts entries whose ESPN
        pairing changed.
        """
        current = {match.get("matchID") for match in matches}
        for match_id in [key for key in self._entries if key not in current]:
            del self._entries[match_id]

        due: list[Mapping[str, Any]] = []
        for match in matches:
            espn_id = match.get("espn_id")
            status = match.get("status")
            if not espn_id:
                continue
            entry = self._entries.get(match.get("matchID"))
            if entry is not None and entry.espn_id != str(espn_id):
                entry = None
                del self._entries[match.get("matchID")]
            if status == "post" and entry is not None and entry.post_since is None:
                entry.post_since = now
            if status not in SUMMARY_STATUSES:
                continue
            if entry is None:
                self._entries[match.get("matchID")] = SummaryEntry(
                    espn_id=str(espn_id), post_since=now if status == "post" else None
                )
                due.append(match)
            elif entry.fetched_status != status or (
                not entry.final
                and (
                    entry.fetched_at is None
                    or now - entry.fetched_at >= SUMMARY_REFRESH - _REFRESH_SLACK
                )
            ):
                due.append(match)
        return due

    def store(
        self,
        match: Mapping[str, Any],
        summary: Mapping[str, Any] | None,
        now: datetime,
    ) -> None:
        """Record a fetched summary (``None``: the fetch failed, keep details).

        A failed fetch counts as a fetch for the timing, so a summary ESPN does
        not have (HTTP 404) is retried every three minutes, not every minute.
        """
        entry = self._entries.get(match.get("matchID"))
        if entry is None:
            return
        status = str(match.get("status"))
        if entry.fetched_status != status:
            entry.failures = 0
        entry.fetched_at = now
        entry.fetched_status = status
        if summary is None:
            entry.failures += 1
            if entry.fetched_status == "post" and entry.failures >= MAX_POST_FAILURES:
                entry.final = True
            return
        entry.failures = 0
        fresh = parse_summary(summary, match.get("espn_home_team_id"))
        for key, value in fresh.items():
            if value is not None:
                entry.details[key] = value
        if entry.fetched_status == "post":
            kickoff = parse_utc(match.get("matchDateTimeUTC"))
            post_since = entry.post_since or now
            entry.final = now - post_since >= SUMMARY_SETTLE or (
                kickoff is not None and now - kickoff >= SETTLED_AFTER_KICKOFF
            )

    def apply(self, matches: Iterable[MutableMapping[str, Any]]) -> None:
        """Write the known details into the matches (in place)."""
        for match in matches:
            entry = self._entries.get(match.get("matchID"))
            if entry is not None:
                match.update(entry.details)

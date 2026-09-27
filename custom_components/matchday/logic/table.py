"""League table: ESPN standings with OpenLigaDB names, plus the live delta.

Mirrors the "<Liga> Tabelle" and "<Liga> Tabelle LIVE" Jinja blocks
(templates.yaml, state 2026-09-27): the numbers come from the ESPN standings,
team name, short name and crest from OpenLigaDB (``getbltable``). The live table
adds the running matches of the matchday on top.

One addition to the Jinja: a match that just ended keeps counting until ESPN has
booked it (``TableTracker``); the Jinja live table lost it between the final
whistle and the next standings update.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Any

from . import parse_utc

LIVE_STATUSES = frozenset({"live", "ht"})

# A finished match counts in the live table until ESPN's ``gamesPlayed`` of the
# team grows, at most this long after the final whistle (an abandoned match
# might never be booked).
PENDING_TIMEOUT = timedelta(hours=3)

# Standings refresh: often while a finished match waits to be booked, a bit
# more often around running or just finished matches, rarely otherwise.
STANDINGS_PENDING = timedelta(minutes=5)
STANDINGS_ACTIVE = timedelta(minutes=10)
STANDINGS_IDLE = timedelta(minutes=30)
RECENTLY_FINISHED = timedelta(hours=5)

_TABLE_STOPWORDS = frozenset(
    {"1", "fc", "sv", "tsg", "rb", "sc", "fsv", "borussia"}
    | {"afc", "vfl", "vfb", "tsv", "bsc"}
)
_TABLE_REPLACEMENTS = (
    ("&", "and"),
    ("ü", "u"),
    ("ö", "o"),
    ("ä", "a"),
    ("ß", "ss"),
    ("munich", "munchen"),
    ("cologne", "koln"),
    ("hamburger", "hamburg"),
    ("monchengladbach", "gladbach"),
)
_DIGITS_AND_DOTS = re.compile(r"[0-9.]")


def norm_table(name: str) -> str:
    """Normalize a team name the way the table Jinja blocks do (all leagues).

    Broader than the matchday normalizers: more club-form words (VfL, VfB, AFC,
    BSC, ...) and "Mönchengladbach" -> "gladbach".
    """
    normalized = (name or "").lower()
    for old, new in _TABLE_REPLACEMENTS:
        normalized = normalized.replace(old, new)
    normalized = _DIGITS_AND_DOTS.sub(" ", normalized)
    return " ".join(
        sorted(word for word in normalized.split() if word not in _TABLE_STOPWORDS)
    )


def _int(value: Any) -> int:
    """Jinja's ``value | float(0) | round | int``."""
    try:
        return round(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _oldb_by_name(
    oldb_table: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    names: dict[str, Mapping[str, Any]] = {}
    for team in oldb_table:
        if isinstance(team, Mapping) and isinstance(team.get("teamName"), str):
            names[norm_table(team["teamName"])] = team
    return names


def _by_word_subset(
    espn_name: str, names: Mapping[str, Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """Return the only OpenLigaDB team whose words contain or are in ESPN's.

    Catches ESPN "Hertha Berlin" vs OpenLigaDB "Hertha BSC" or "Arminia
    Bielefeld" vs "DSC Arminia Bielefeld". Ambiguous -> ``None``.
    """
    espn_words = norm_table(espn_name).split()
    hits = []
    for key, team in names.items():
        words = key.split()
        common = sum(1 for word in words if word in espn_words)
        if common > 0 and common in (len(words), len(espn_words)):
            hits.append(team)
    return hits[0] if len(hits) == 1 else None


def build_table(
    entries: Sequence[Mapping[str, Any]],
    oldb_table: Iterable[Mapping[str, Any]],
    matches: Sequence[Mapping[str, Any]],
    previous: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the table in ESPN's order (by rank), without live delta.

    Names come, in this order, from (1) the matchday: the OpenLigaDB team of a
    match paired with the ESPN team id, (2) the OpenLigaDB table by normalized
    name, exactly or by word subset, (3) the previous table by ESPN team id,
    (4) ESPN itself.
    """
    names = _oldb_by_name(oldb_table)
    previous_by_id = {
        str(team.get("espn_team_id")): team
        for team in previous or []
        if isinstance(team, Mapping) and team.get("espn_team_id") is not None
    }
    table: list[dict[str, Any]] = []
    for entry in entries:
        team = _mapping(entry.get("team"))
        team_id = str(team.get("id"))
        display_name = str(team.get("displayName") or "")
        stats: dict[str, int] = {}
        for stat in entry.get("stats") or []:
            if isinstance(stat, Mapping) and "name" in stat:
                stats[str(stat["name"])] = _int(stat.get("value"))

        home = next((m for m in matches if m.get("espn_home_team_id") == team_id), None)
        away = next((m for m in matches if m.get("espn_away_team_id") == team_id), None)
        source: Mapping[str, Any]
        if home is not None:
            source = _mapping(home.get("team1"))
        elif away is not None:
            source = _mapping(away.get("team2"))
        elif (
            hit := names.get(norm_table(display_name))
            or _by_word_subset(display_name, names)
        ) is not None:
            source = hit
        else:
            source = previous_by_id.get(team_id, {})

        logos = team.get("logos") or []
        espn_logo = (
            _mapping(logos[0]).get("href", "")
            if isinstance(logos, list) and logos
            else ""
        )
        goals, against = stats.get("pointsFor", 0), stats.get("pointsAgainst", 0)
        table.append(
            {
                "espn_team_id": team_id,
                "teamName": source.get("teamName") or display_name,
                "shortName": source.get("shortName") or team.get("abbreviation", ""),
                "teamIconUrl": source.get("teamIconUrl") or espn_logo,
                "matches": stats.get("gamesPlayed", 0),
                "won": stats.get("wins", 0),
                "draw": stats.get("ties", 0),
                "lost": stats.get("losses", 0),
                "goals": goals,
                "opponentGoals": against,
                "diff": goals - against,
                "points": stats.get("points", 0),
            }
        )
    return table


def _score(live_score: Any) -> tuple[int, int] | None:
    """Split "h:a" like Jinja (``int(0)`` for unparsable parts)."""
    if not live_score or not isinstance(live_score, str):
        return None
    parts = live_score.split(":")

    def number(index: int) -> int:
        try:
            return int(parts[index])
        except (IndexError, ValueError):
            return 0

    return number(0), number(1)


def live_table(
    base: Sequence[Mapping[str, Any]],
    counted: Sequence[tuple[Mapping[str, Any], frozenset[str]]],
) -> list[dict[str, Any]]:
    """Add the counted matches to ``base`` and sort by points, diff, goals.

    ``counted`` pairs each match with the ESPN team ids it still counts for
    (running matches: both teams; finished ones until ESPN booked them). A team
    is flagged ``live`` (with its ``live_score``) only for running matches.
    Ties keep the ESPN order, like the stable sorts of the Jinja block.
    """
    rows: list[dict[str, Any]] = []
    for team in base:
        team_id = team.get("espn_team_id")
        played = won = draw = lost = goals_for = goals_against = points = 0
        live = False
        live_score: str | None = None
        for match, team_ids in counted:
            if team_id is None or team_id not in team_ids:
                continue
            is_home = match.get("espn_home_team_id") == team_id
            is_away = match.get("espn_away_team_id") == team_id
            score = _score(match.get("live_score"))
            if not (is_home or is_away) or score is None:
                continue
            scored, conceded = score if is_home else (score[1], score[0])
            played += 1
            goals_for += scored
            goals_against += conceded
            won += scored > conceded
            draw += scored == conceded
            lost += scored < conceded
            points += 3 if scored > conceded else 1 if scored == conceded else 0
            if match.get("status") in LIVE_STATUSES:
                live = True
                live_score = f"{scored}:{conceded}"
        goals = _int(team.get("goals")) + goals_for
        against = _int(team.get("opponentGoals")) + goals_against
        rows.append(
            {
                **team,
                "goals": goals,
                "opponentGoals": against,
                "points": _int(team.get("points")) + points,
                "matches": _int(team.get("matches")) + played,
                "won": _int(team.get("won")) + won,
                "draw": _int(team.get("draw")) + draw,
                "lost": _int(team.get("lost")) + lost,
                "diff": goals - against,
                "live": live,
                "live_score": live_score,
            }
        )
    return sorted(rows, key=lambda row: (-row["points"], -row["diff"], -row["goals"]))


@dataclass(slots=True)
class _Snapshot:
    """``gamesPlayed`` of both teams while a match was running."""

    games: dict[str, int]
    finished_at: datetime | None = None
    booked: set[str] = field(default_factory=set)


class TableTracker:
    """Decide which matches the live table counts on top of the standings.

    Running matches (live, half time) always count. When a match is first seen
    running, the ``gamesPlayed`` of both teams in the current standings is
    remembered. After the final whistle the match keeps counting for a team as
    long as its ``gamesPlayed`` is unchanged, i.e. ESPN has not booked the match
    yet; at most ``PENDING_TIMEOUT`` after the whistle. Without a snapshot (the
    match ended before Home Assistant started) a finished match is not counted:
    a short gap is the smaller error than counting a match twice.
    """

    def __init__(self) -> None:
        """Start without snapshots."""
        self._snapshots: dict[Any, _Snapshot] = {}

    def counted(
        self,
        matches: Sequence[Mapping[str, Any]],
        base: Sequence[Mapping[str, Any]] | None,
        now: datetime,
    ) -> list[tuple[Mapping[str, Any], frozenset[str]]]:
        """Update the snapshots and return the matches to add to ``base``."""
        games = {
            str(team.get("espn_team_id")): _int(team.get("matches"))
            for team in base or []
        }
        current = {match.get("matchID") for match in matches}
        for match_id in [key for key in self._snapshots if key not in current]:
            del self._snapshots[match_id]

        counted: list[tuple[Mapping[str, Any], frozenset[str]]] = []
        for match in matches:
            home, away = match.get("espn_home_team_id"), match.get("espn_away_team_id")
            if home is None or away is None:
                continue
            team_ids = (str(home), str(away))
            status = match.get("status")
            snapshot = self._snapshots.get(match.get("matchID"))
            if status in LIVE_STATUSES:
                if snapshot is None and all(team in games for team in team_ids):
                    self._snapshots[match.get("matchID")] = _Snapshot(
                        {team: games[team] for team in team_ids}
                    )
                counted.append((match, frozenset(team_ids)))
            elif status == "post" and snapshot is not None:
                if snapshot.finished_at is None:
                    snapshot.finished_at = now
                if now - snapshot.finished_at >= PENDING_TIMEOUT:
                    continue
                for team in team_ids:
                    if team in games and games[team] != snapshot.games.get(team):
                        snapshot.booked.add(team)
                open_teams = frozenset(set(team_ids) - snapshot.booked)
                if open_teams:
                    counted.append((match, open_teams))
        return counted

    def has_pending(
        self, counted: Sequence[tuple[Mapping[str, Any], frozenset[str]]]
    ) -> bool:
        """Return True if a finished match still waits to be booked."""
        return any(match.get("status") == "post" for match, _teams in counted)


def standings_interval(
    matches: Sequence[Mapping[str, Any]], pending: bool, now: datetime
) -> timedelta:
    """Return how old the standings may be before they are fetched again."""
    if pending:
        return STANDINGS_PENDING
    for match in matches:
        status = match.get("status")
        if status in LIVE_STATUSES:
            return STANDINGS_ACTIVE
        kickoff = parse_utc(match.get("matchDateTimeUTC"))
        if (
            status == "post"
            and kickoff is not None
            and timedelta(0) <= now - kickoff <= RECENTLY_FINISHED
        ):
            return STANDINGS_ACTIVE
    return STANDINGS_IDLE

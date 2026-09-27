"""Merge OpenLigaDB fixtures with ESPN live data.

OpenLigaDB defines the matchday (pairings, German names, crests, match ids), ESPN
supplies status, score, scorers and cards. The output format is the ``matches``
attribute of the Jinja template sensors this integration replaces, so the
existing dashboard card keeps working: each OpenLigaDB match dict, enriched with
``espn_id``, ``espn_home_team_id``, ``espn_away_team_id``, ``status``, ``minute``,
``live_score``, ``scorers``, ``cards`` and ``oldb_score`` (plus the summary
fields, see ``SUMMARY_PLACEHOLDERS``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
import re
from typing import Any, Literal

from . import parse_utc

type Normalizer = Callable[[str], str]
type MatchStatus = Literal["pre", "live", "ht", "post"]

# Stage 2 tolerance: same pairing, kickoff moved by up to four days (Fri -> Tue).
RESCHEDULE_TOLERANCE = timedelta(seconds=345600)

# Summary fields (substitutions, stats, lineups, half-time score, penalty
# shootout) come from ESPN match summaries (``logic/summary.py``). Until a
# summary is known they carry the values the Jinja sensor uses without one, so
# consumers of the ``matches`` format find every key they expect.
SUMMARY_PLACEHOLDERS: dict[str, Any] = {
    "substitutions": [],
    "stats": None,
    "lineups": None,
    "ht_score": None,
    "pso": None,
}

# "tsv" is a stop word only in the 2. Bundesliga and DFB-Pokal Jinja blocks; no
# Bundesliga club carries it, and dropping it on both sides can only help
# ("TSV Eintracht Braunschweig" at ESPN vs "Eintracht Braunschweig").
_DE_STOPWORDS = frozenset(
    {"1", "fc", "sv", "tsg", "rb", "sc", "fsv", "borussia", "tsv"}
)
_DE_REPLACEMENTS = (
    ("ü", "u"),
    ("ö", "o"),
    ("ä", "a"),
    ("ß", "ss"),
    ("munich", "munchen"),
    ("cologne", "koln"),
    ("hamburger", "hamburg"),
)
_DIGITS_AND_DOTS = re.compile(r"[0-9.]")
_MINUTE = re.compile(r"\s*(\d+)\s*'?\s*(?:\+\s*(\d+))?")


def norm_de(name: str) -> str:
    """Normalize a team name the way the German-league Jinja blocks do.

    Lowercase, transliterate umlauts, map English city names (Munich, Cologne),
    drop digits/dots and club-form words (FC, SV, TSG, ...), sort the rest.
    "1. FSV Mainz 05" and "Mainz" both become "mainz".
    """
    normalized = (name or "").lower()
    for old, new in _DE_REPLACEMENTS:
        normalized = normalized.replace(old, new)
    normalized = _DIGITS_AND_DOTS.sub(" ", normalized)
    return " ".join(
        sorted(word for word in normalized.split() if word not in _DE_STOPWORDS)
    )


def norm_en(name: str) -> str:
    """Normalize a team name the way the Premier League Jinja block does.

    Lowercase, ``&`` -> ``and``, drop dots and the word "fc", sort the words.
    """
    normalized = (name or "").lower().replace("&", "and").replace(".", "")
    return " ".join(sorted(word for word in normalized.split() if word != "fc"))


NORMALIZERS: dict[str, Normalizer] = {"de": norm_de, "en": norm_en}


def match_status(
    oldb_finished: bool, espn_state: str, espn_type_name: str
) -> MatchStatus:
    """Map OpenLigaDB/ESPN status fields to pre/live/ht/post."""
    if oldb_finished or espn_state == "post":
        return "post"
    if espn_state == "in":
        return "ht" if espn_type_name.lower() == "status_halftime" else "live"
    return "pre"


def minute_sort_key(display_value: str, clock_value: float) -> tuple[int, int, float]:
    """Return a sort key for a match event.

    ESPN caps ``clock.value`` at 45:00 / 90:00: every stoppage-time event has the
    same value (2700 / 5400). Only ``clock.displayValue`` ("90'+5'") tells them
    apart, so it is the primary key; the value only breaks ties.
    """
    if found := _MINUTE.match(display_value or ""):
        return int(found[1]), int(found[2] or 0), clock_value
    return int(clock_value // 60) + 1, 0, clock_value


@dataclass(slots=True)
class ParsedEvent:
    """An ESPN event reduced to what the matching and enrichment need."""

    id: str
    kickoff: datetime
    home_norm: str
    away_norm: str
    competition: Mapping[str, Any]
    home: Mapping[str, Any]
    away: Mapping[str, Any]


def _competitor(competition: Mapping[str, Any], side: str) -> Mapping[str, Any] | None:
    for competitor in competition.get("competitors") or []:
        if isinstance(competitor, Mapping) and competitor.get("homeAway") == side:
            return competitor
    return None


def _team(competitor: Mapping[str, Any]) -> Mapping[str, Any]:
    team = competitor.get("team")
    return team if isinstance(team, Mapping) else {}


def _parse_events(
    events: Sequence[Mapping[str, Any]], normalize: Normalizer
) -> list[ParsedEvent]:
    parsed: list[ParsedEvent] = []
    for event in events:
        competition = event["competitions"][0]
        home = _competitor(competition, "home")
        away = _competitor(competition, "away")
        kickoff = parse_utc(competition.get("date"))
        if home is None or away is None or kickoff is None:
            continue
        parsed.append(
            ParsedEvent(
                id=str(event["id"]),
                kickoff=kickoff,
                home_norm=normalize(str(_team(home).get("displayName", ""))),
                away_norm=normalize(str(_team(away).get("displayName", ""))),
                competition=competition,
                home=home,
                away=away,
            )
        )
    return parsed


def _minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _team_names(match: Mapping[str, Any], normalize: Normalizer) -> tuple[str, str]:
    team1 = match.get("team1") or {}
    team2 = match.get("team2") or {}
    return (
        normalize(str(team1.get("teamName", ""))),
        normalize(str(team2.get("teamName", ""))),
    )


def pair_matches(
    oldb_matches: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    normalize: Normalizer,
) -> dict[int, ParsedEvent]:
    """Pair OpenLigaDB matches with ESPN events; returns match index -> event.

    Three stages, each ESPN event used at most once:

    1. kickoff equal to the minute and both normalized team names equal,
    2. same pairing with a different kickoff within four days (rescheduled game),
    3. time-slot fallback: exactly one unpaired OpenLigaDB match and exactly one
       unused ESPN event share the kickoff minute (names spelled too differently).
    """
    parsed = _parse_events(events, normalize)
    kickoffs = [parse_utc(match.get("matchDateTimeUTC")) for match in oldb_matches]
    names = [_team_names(match, normalize) for match in oldb_matches]
    paired: dict[int, ParsedEvent] = {}
    used: set[str] = set()

    def claim(index: int, predicate: Callable[[ParsedEvent], bool]) -> None:
        for event in parsed:
            if event.id not in used and predicate(event):
                paired[index] = event
                used.add(event.id)
                return

    # Stage 1: kickoff minute + names.
    for index, kickoff in enumerate(kickoffs):
        if kickoff is None:
            continue
        slot, (home, away) = _minute(kickoff), names[index]
        claim(
            index,
            lambda e, slot=slot, home=home, away=away: (
                _minute(e.kickoff) == slot
                and e.home_norm == home
                and e.away_norm == away
            ),
        )

    # Stage 2: names only, kickoff within the reschedule tolerance.
    for index, kickoff in enumerate(kickoffs):
        if index in paired or kickoff is None:
            continue
        home, away = names[index]
        claim(
            index,
            lambda e, kickoff=kickoff, home=home, away=away: (
                e.home_norm == home
                and e.away_norm == away
                and abs(e.kickoff - kickoff) <= RESCHEDULE_TOLERANCE
            ),
        )

    # Stage 3: unique open match per kickoff slot on both sides.
    for index, kickoff in enumerate(kickoffs):
        if index in paired or kickoff is None:
            continue
        slot = _minute(kickoff)
        open_oldb = sum(
            1
            for other, other_kickoff in enumerate(kickoffs)
            if other not in paired
            and other_kickoff is not None
            and _minute(other_kickoff) == slot
        )
        open_espn = [
            e for e in parsed if e.id not in used and _minute(e.kickoff) == slot
        ]
        if open_oldb == 1 and len(open_espn) == 1:
            paired[index] = open_espn[0]
            used.add(open_espn[0].id)

    return paired


def _oldb_score(match: Mapping[str, Any]) -> str | None:
    """Return the OpenLigaDB final score ("h:a", result type 2), if any."""
    for result in match.get("matchResults") or []:
        if isinstance(result, Mapping) and result.get("resultTypeID") == 2:
            return f"{result.get('pointsTeam1')}:{result.get('pointsTeam2')}"
    return None


def _score(value: Any) -> str:
    return "" if value is None else str(value)


def extract_details(
    competition: Mapping[str, Any], home_team_id: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (scorers, cards) from ``competitions[0].details``, chronologically."""
    scorers: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for detail in competition.get("details") or []:
        if not isinstance(detail, Mapping):
            continue
        athletes = detail.get("athletesInvolved") or []
        name = (
            str(athletes[0].get("displayName", ""))
            if athletes and isinstance(athletes[0], Mapping)
            else ""
        )
        clock = detail.get("clock") if isinstance(detail.get("clock"), Mapping) else {}
        entry = {
            "name": name,
            "minute": clock.get("displayValue", ""),
            "sortKey": clock.get("value", 0),
            "team": "team1"
            if (detail.get("team") or {}).get("id", "") == home_team_id
            else "team2",
        }
        if detail.get("scoringPlay"):
            if detail.get("penaltyKick"):
                goal_type = "penalty"
            elif detail.get("ownGoal"):
                goal_type = "owngoal"
            else:
                goal_type = "regular"
            scorers.append({**entry, "type": goal_type})
        elif detail.get("redCard"):
            cards.append({**entry, "type": "red"})
        elif detail.get("yellowCard"):
            cards.append({**entry, "type": "yellow"})

    def order(item: dict[str, Any]) -> tuple[int, int, float]:
        try:
            value = float(item["sortKey"] or 0)
        except (TypeError, ValueError):
            value = 0.0
        return minute_sort_key(str(item["minute"] or ""), value)

    # sorted() is stable: events in the same displayed minute keep ESPN's order.
    return sorted(scorers, key=order), sorted(cards, key=order)


def _enrich(
    match: Mapping[str, Any], event: ParsedEvent, local_tz: tzinfo
) -> dict[str, Any]:
    competition = event.competition
    home, away = event.home, event.away
    status_block = competition.get("status") or {}
    status_type = status_block.get("type") or {}
    status = match_status(
        bool(match.get("matchIsFinished")),
        str(status_type.get("state") or ""),
        str(status_type.get("name") or ""),
    )
    home_team_id = _team(home).get("id")
    scorers, cards = extract_details(competition, home_team_id)
    kickoff = event.kickoff
    return {
        **match,
        "oldb_score": _oldb_score(match),
        # ESPN knows reschedules earlier than OpenLigaDB; its kickoff wins.
        "matchDateTimeUTC": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "matchDateTime": kickoff.astimezone(local_tz).strftime("%Y-%m-%dT%H:%M:%S"),
        "espn_id": event.id,
        "espn_home_team_id": home_team_id,
        "espn_away_team_id": _team(away).get("id"),
        "minute": status_block.get("displayClock"),
        "live_score": f"{_score(home.get('score'))}:{_score(away.get('score'))}"
        if status in ("live", "ht", "post")
        else None,
        "scorers": scorers,
        "cards": cards,
        "status": status,
        **SUMMARY_PLACEHOLDERS,
    }


def _oldb_only(match: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **match,
        "oldb_score": _oldb_score(match),
        "espn_id": None,
        "espn_home_team_id": None,
        "espn_away_team_id": None,
        "minute": None,
        "live_score": None,
        "scorers": [],
        "cards": [],
        "status": "post" if match.get("matchIsFinished") else "pre",
        **SUMMARY_PLACEHOLDERS,
    }


def merge_matches(
    oldb_matches: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    normalize: Normalizer,
    local_tz: tzinfo,
    *,
    oldb_results: bool = True,
) -> list[dict[str, Any]]:
    """Return the enriched ``matches`` list, sorted by (possibly corrected) kickoff.

    With ``oldb_results=False`` (Premier League) ``matchResults`` is emptied after
    ``oldb_score`` was taken from it, like the Jinja block does: OpenLigaDB only
    holds 0:0 placeholders there.
    """
    paired = pair_matches(oldb_matches, events, normalize)
    merged = [
        _enrich(match, paired[index], local_tz)
        if index in paired
        else _oldb_only(match)
        for index, match in enumerate(oldb_matches)
    ]
    if not oldb_results:
        for match in merged:
            match["matchResults"] = []
    return sorted(merged, key=lambda match: str(match.get("matchDateTimeUTC") or ""))

"""Competitions without OpenLigaDB (UEFA): rounds and matches from ESPN alone.

Mirrors the UEFA Jinja blocks (templates.yaml, state 2026-10-12) in the output
format: ``matchID`` is the ESPN event id as int, ``team1``/``team2`` carry ESPN's
name, abbreviation and logo, ``espn_slug`` the slug the event came from (main or
qualifying), ``matchResults`` is empty.

ESPN has no matchday numbers. The rounds of a season are cut from its events:
two-legged ties (qualifying, knockout) carry ``competitions[0].leg``, so each leg
of a stage (``event.season.slug``: ``first-round``, ``round-of-16``, ...) is one
round, however its kickoffs spread. Events without a leg (league phase, final)
are cut by kickoff: a new round after a gap of more than ``ROUND_GAP`` or when
the stage changes. The UEFA weeks are Tuesday to Thursday, consecutive rounds at
least five days apart.

Each match carries ``phase`` (the stage slug), ``leg`` (1, 2 or ``None``) and, in
the second leg of a tie, ``aggregate`` (``annotate_ties``): the first leg's score
and the aggregate, both from this match's ``team1``/``team2`` side, and which side
advanced once the tie is decided. The penalty shootout (``pso``) comes from the
scoreboard right away, before any summary.

Two things differ from the Jinja on purpose: the shown round is picked like for
the OpenLigaDB leagues (the round closer in time; the Jinja jumps to the next
round as soon as the last match ended), and finished matches follow the summary
policy of ``logic/summary.py`` (the Jinja fetches a finished summary once).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from . import parse_utc
from .merge import SUMMARY_PLACEHOLDERS, extract_details
from .summary import pso_from_competitors

type EspnEvent = Mapping[str, Any]

ROUND_GAP = timedelta(days=3)
LEAGUE_PHASE = "league-phase"

# German names of the stages (``event.season.slug``); unknown ones are spelled
# out from the slug.
STAGE_NAMES: dict[str, str] = {
    "preliminary-round": "Vorrunde",
    "first-round": "1. Qualifikationsrunde",
    "second-round": "2. Qualifikationsrunde",
    "third-round": "3. Qualifikationsrunde",
    "playoff-round": "Qualifikation Play-offs",
    "knockout-round-playoffs": "K.-o.-Play-offs",
    "round-of-16": "Achtelfinale",
    "quarterfinals": "Viertelfinale",
    "semifinals": "Halbfinale",
    "final": "Finale",
}
LEG_NAMES = ("Hinspiel", "Rückspiel")


@dataclass(frozen=True, slots=True)
class Round:
    """One round of a season: a matchday of the league phase or one leg of a stage."""

    number: int
    name: str
    stage: str
    # 1 or 2 for a leg of two-legged ties, ``None`` otherwise.
    leg: int | None
    first_kickoff: datetime
    last_kickoff: datetime
    # ESPN event id -> slug it came from.
    events: Mapping[str, str]

    @property
    def slugs(self) -> list[str]:
        """The slugs to ask for this round's events, in a stable order."""
        return sorted(set(self.events.values()))

    def as_matchday(self) -> dict[str, Any]:
        """Return the round in the format of the ``matchdays`` attribute."""
        return {
            "matchday": self.number,
            "name": self.name,
            "phase": self.stage,
            "first_kickoff": _iso(self.first_kickoff),
            "last_kickoff": _iso(self.last_kickoff),
        }


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _kickoff(event: EspnEvent) -> datetime | None:
    competitions = event.get("competitions") or [{}]
    return parse_utc(competitions[0].get("date"))


def _stage(event: EspnEvent) -> str:
    season = event.get("season")
    return str(season.get("slug") or "") if isinstance(season, Mapping) else ""


def _leg(event: EspnEvent) -> int | None:
    """Return the leg of a two-legged tie (1, 2), ``None`` for single matches."""
    competitions = event.get("competitions") or [{}]
    leg = competitions[0].get("leg")
    value = leg.get("value") if isinstance(leg, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value >= 1 else None


def stage_name(stage: str) -> str:
    """Return the German name of a stage slug."""
    return STAGE_NAMES.get(stage) or stage.replace("-", " ").title() or "Runde"


def season_rounds(events: Iterable[tuple[str, EspnEvent]]) -> list[Round]:
    """Cut the season's events (slug, event) into rounds, numbered by kickoff.

    One round per stage and leg; events without a leg are cut by kickoff gaps.
    League phase rounds are named "N. Spieltag", legs "<stage> · Hinspiel" /
    "Rückspiel", a stage with several rounds without legs "<stage> (i/n)". Each
    event id counts once.
    """
    seen: dict[str, tuple[datetime, str, int | None, str]] = {}
    for slug, event in events:
        kickoff = _kickoff(event)
        event_id = str(event.get("id", ""))
        if kickoff is None or not event_id or event_id in seen:
            continue
        seen[event_id] = (kickoff, _stage(event), _leg(event), slug)

    type Entry = tuple[datetime, str, int | None, str, str]
    groups: list[list[Entry]] = []
    by_leg: dict[tuple[str, int], list[Entry]] = {}
    open_by_stage: dict[str, list[Entry]] = {}
    for event_id, (kickoff, stage, leg, slug) in sorted(
        seen.items(), key=lambda item: (item[1][0], item[0])
    ):
        entry = (kickoff, stage, leg, slug, event_id)
        if leg is not None:
            group = by_leg.get((stage, leg))
            if group is None:
                group = by_leg[(stage, leg)] = []
                groups.append(group)
        else:
            group = open_by_stage.get(stage)
            if group is None or kickoff - group[-1][0] > ROUND_GAP:
                group = open_by_stage[stage] = []
                groups.append(group)
        group.append(entry)
    groups.sort(key=lambda group: (group[0][0], group[0][4]))

    plain_per_stage: dict[str, int] = {}
    for group in groups:
        if group[0][2] is None:
            plain_per_stage[group[0][1]] = plain_per_stage.get(group[0][1], 0) + 1
    rounds: list[Round] = []
    plain_index: dict[str, int] = {}
    for number, group in enumerate(groups, start=1):
        stage, leg = group[0][1], group[0][2]
        if leg is not None:
            leg_name = LEG_NAMES[leg - 1] if leg <= len(LEG_NAMES) else f"{leg}. Spiel"
            name = f"{stage_name(stage)} · {leg_name}"
        else:
            plain_index[stage] = plain_index.get(stage, 0) + 1
            index, total = plain_index[stage], plain_per_stage[stage]
            if stage == LEAGUE_PHASE:
                name = f"{index}. Spieltag"
            elif total == 1:
                name = stage_name(stage)
            else:
                name = f"{stage_name(stage)} ({index}/{total})"
        rounds.append(
            Round(
                number=number,
                name=name,
                stage=stage,
                leg=leg,
                first_kickoff=group[0][0],
                last_kickoff=group[-1][0],
                events={event_id: slug for *_rest, slug, event_id in group},
            )
        )
    return rounds


def pick_round(rounds: Sequence[Round], now: datetime) -> Round | None:
    """Choose the round to show: the one closer in time, like ``pick_matchday``.

    "Last" is the latest round that has kicked off, "next" the earliest that has
    not; ``now - last.last_kickoff <= next.first_kickoff - now`` keeps the last.
    """
    last = next((r for r in reversed(rounds) if r.first_kickoff <= now), None)
    upcoming = next((r for r in rounds if r.first_kickoff > now), None)
    if last is None or upcoming is None:
        return last or upcoming
    return last if now - last.last_kickoff <= upcoming.first_kickoff - now else upcoming


def _status(competition: Mapping[str, Any]) -> tuple[str, bool]:
    """Return (pre/live/ht/post, completed) as the UEFA Jinja blocks map it."""
    status_type = (competition.get("status") or {}).get("type") or {}
    completed = bool(status_type.get("completed"))
    state = str(status_type.get("state") or "")
    if completed:
        return "post", True
    if state == "in":
        name = str(status_type.get("name") or "").lower()
        return ("ht" if name == "status_halftime" else "live"), False
    return "pre", False


def _side(competition: Mapping[str, Any], side: str) -> Mapping[str, Any]:
    for competitor in competition.get("competitors") or []:
        if isinstance(competitor, Mapping) and competitor.get("homeAway") == side:
            return competitor
    return {}


def _team(competitor: Mapping[str, Any]) -> Mapping[str, Any]:
    team = competitor.get("team")
    return team if isinstance(team, Mapping) else {}


def _score(value: Any) -> str:
    return "" if value is None else str(value)


def espn_match(event: EspnEvent, slug: str, local_tz: tzinfo) -> dict[str, Any] | None:
    """Return an ESPN event in the ``matches`` format, ``None`` if unusable."""
    competition = (event.get("competitions") or [{}])[0]
    kickoff = _kickoff(event)
    home, away = _side(competition, "home"), _side(competition, "away")
    if kickoff is None or not home or not away:
        return None
    status, completed = _status(competition)
    home_team, away_team = _team(home), _team(away)
    scorers, cards = extract_details(competition, home_team.get("id"))
    pso = pso_from_competitors(home, away)
    return {
        "matchID": int(event["id"]),
        "matchDateTime": kickoff.astimezone(local_tz).strftime("%Y-%m-%dT%H:%M:%S"),
        "matchDateTimeUTC": _iso(kickoff),
        "matchIsFinished": completed,
        "matchResults": [],
        "team1": {
            "teamName": home_team.get("displayName", ""),
            "shortName": home_team.get("abbreviation", ""),
            "teamIconUrl": home_team.get("logo", ""),
        },
        "team2": {
            "teamName": away_team.get("displayName", ""),
            "shortName": away_team.get("abbreviation", ""),
            "teamIconUrl": away_team.get("logo", ""),
        },
        "oldb_score": None,
        "espn_id": str(event["id"]),
        "espn_slug": slug,
        "espn_home_team_id": home_team.get("id"),
        "espn_away_team_id": away_team.get("id"),
        "minute": (competition.get("status") or {}).get("displayClock"),
        "live_score": f"{_score(home.get('score'))}:{_score(away.get('score'))}"
        if status in ("live", "ht", "post")
        else None,
        "scorers": scorers,
        "cards": cards,
        "status": status,
        "phase": _stage(event),
        "leg": _leg(event),
        "aggregate": None,
        **SUMMARY_PLACEHOLDERS,
        "pso": pso,
    }


def round_matches(
    current: Round,
    events: Iterable[tuple[str, EspnEvent]],
    local_tz: tzinfo,
) -> list[dict[str, Any]]:
    """Return the matches of ``current`` from events, sorted by kickoff.

    Events outside the round are ignored; an event of the round missing from the
    answer is left out (the caller guards against an empty result). An event
    listed twice counts in its last version (pass fresh events last).
    """
    matches: dict[str, dict[str, Any]] = {}
    for slug, event in events:
        event_id = str(event.get("id", ""))
        if event_id not in current.events:
            continue
        if (match := espn_match(event, slug, local_tz)) is not None:
            matches[event_id] = match
    return sorted(matches.values(), key=lambda m: (m["matchDateTimeUTC"], m["matchID"]))


def _goals(competitor: Mapping[str, Any], key: str = "score") -> int | None:
    try:
        return int(float(competitor.get(key)))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class _FirstLeg:
    """The result of a finished first leg, from its home team's side."""

    home_goals: int
    away_goals: int


def _first_legs(
    events: Iterable[tuple[str, EspnEvent]],
) -> dict[tuple[str, str, str], _FirstLeg]:
    """Finished first legs by (stage, home team id, away team id)."""
    legs: dict[tuple[str, str, str], _FirstLeg] = {}
    for _slug, event in events:
        if _leg(event) != 1:
            continue
        competition = (event.get("competitions") or [{}])[0]
        if _status(competition)[0] != "post":
            continue
        home, away = _side(competition, "home"), _side(competition, "away")
        home_goals, away_goals = _goals(home), _goals(away)
        home_id, away_id = _team(home).get("id"), _team(away).get("id")
        if None in (home_goals, away_goals, home_id, away_id):
            continue
        legs[(_stage(event), str(home_id), str(away_id))] = _FirstLeg(
            home_goals, away_goals
        )
    return legs


def _advances(event: EspnEvent, match: Mapping[str, Any]) -> str | None:
    """Return the side ("team1"/"team2") that went through, once ESPN says so."""
    competition = (event.get("competitions") or [{}])[0]
    home, away = _side(competition, "home"), _side(competition, "away")
    if home.get("advance") is True or away.get("advance") is True:
        return "team1" if home.get("advance") is True else "team2"
    series = competition.get("series")
    if isinstance(series, Mapping) and series.get("completed") is True:
        for competitor in series.get("competitors") or []:
            if isinstance(competitor, Mapping) and competitor.get("winner") is True:
                team_id = str(competitor.get("id"))
                if team_id == str(match.get("espn_home_team_id")):
                    return "team1"
                if team_id == str(match.get("espn_away_team_id")):
                    return "team2"
    return None


def annotate_ties(
    matches: Iterable[dict[str, Any]],
    events: Iterable[tuple[str, EspnEvent]],
) -> None:
    """Fill ``aggregate`` of second legs (in place) from the season's events.

    The first leg is the finished leg 1 of the same stage with home and away
    swapped; its score is added to the running score of the second leg (before
    kickoff the aggregate is the first leg's score). Without a known first leg,
    ESPN's ``aggregateScore`` of the competitors is used if present. ``advances``
    is set once the second leg is finished and ESPN names the side that went
    through (``advance`` or the series winner). An event listed twice counts in
    its last version (pass fresh events last).
    """
    events = list(events)
    first_legs = _first_legs(events)
    by_id = {str(event.get("id")): event for _slug, event in events}
    for match in matches:
        if match.get("leg") != 2:
            continue
        event = by_id.get(str(match.get("espn_id")))
        home_id = str(match.get("espn_home_team_id"))
        away_id = str(match.get("espn_away_team_id"))
        first = first_legs.get((str(match.get("phase")), away_id, home_id))
        current = _split(match.get("live_score"))
        first_leg = score = None
        if first is not None:
            # Home team now was away in the first leg.
            first_leg = (first.away_goals, first.home_goals)
            score = (
                first_leg
                if current is None
                else (first_leg[0] + current[0], first_leg[1] + current[1])
            )
        elif event is not None:
            competition = (event.get("competitions") or [{}])[0]
            home, away = _side(competition, "home"), _side(competition, "away")
            home_agg = _goals(home, "aggregateScore")
            away_agg = _goals(away, "aggregateScore")
            if home_agg is not None and away_agg is not None:
                score = (home_agg, away_agg)
        advances = (
            _advances(event, match)
            if event is not None and match.get("status") == "post"
            else None
        )
        if first_leg is None and score is None and advances is None:
            continue
        match["aggregate"] = {
            "first_leg": _join(first_leg),
            "score": _join(score),
            "advances": advances,
        }


def _split(score: Any) -> tuple[int, int] | None:
    if not isinstance(score, str) or ":" not in score:
        return None
    left, right = score.split(":", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return None


def _join(score: tuple[int, int] | None) -> str | None:
    return None if score is None else f"{score[0]}:{score[1]}"

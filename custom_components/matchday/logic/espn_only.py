"""Competitions without OpenLigaDB (UEFA): rounds and matches from ESPN alone.

Mirrors the UEFA Jinja blocks (templates.yaml, state 2026-10-12) in the output
format: ``matchID`` is the ESPN event id as int, ``team1``/``team2`` carry ESPN's
name, abbreviation and logo, ``espn_slug`` the slug the event came from (main or
qualifying), ``matchResults`` is empty.

ESPN has no matchday numbers. The rounds of a season are cut from its kickoffs:
a new round starts after a gap of more than ``ROUND_GAP`` or when the stage
(``event.season.slug``: ``league-phase``, ``round-of-16``, ...) changes. The UEFA
weeks are Tuesday to Thursday, consecutive rounds at least five days apart.

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


def stage_name(stage: str) -> str:
    """Return the German name of a stage slug."""
    return STAGE_NAMES.get(stage) or stage.replace("-", " ").title() or "Runde"


def season_rounds(events: Iterable[tuple[str, EspnEvent]]) -> list[Round]:
    """Cut the season's events (slug, event) into rounds, numbered by kickoff.

    League phase rounds are named "N. Spieltag"; a stage with two rounds gets
    "Hinspiel"/"Rückspiel", one with more "(i/n)". Each event id counts once.
    """
    seen: dict[str, tuple[datetime, str, str]] = {}
    for slug, event in events:
        kickoff = _kickoff(event)
        event_id = str(event.get("id", ""))
        if kickoff is None or not event_id or event_id in seen:
            continue
        seen[event_id] = (kickoff, _stage(event), slug)

    groups: list[list[tuple[datetime, str, str, str]]] = []
    for event_id, (kickoff, stage, slug) in sorted(
        seen.items(), key=lambda item: (item[1][0], item[0])
    ):
        last = groups[-1][-1] if groups else None
        if last is None or kickoff - last[0] > ROUND_GAP or stage != last[1]:
            groups.append([])
        groups[-1].append((kickoff, stage, slug, event_id))

    per_stage: dict[str, int] = {}
    for group in groups:
        per_stage[group[0][1]] = per_stage.get(group[0][1], 0) + 1
    rounds: list[Round] = []
    index_in_stage: dict[str, int] = {}
    for number, group in enumerate(groups, start=1):
        stage = group[0][1]
        index_in_stage[stage] = index_in_stage.get(stage, 0) + 1
        index, total = index_in_stage[stage], per_stage[stage]
        if stage == LEAGUE_PHASE:
            name = f"{index}. Spieltag"
        elif total == 1:
            name = stage_name(stage)
        elif total == 2:
            name = f"{stage_name(stage)} · {LEG_NAMES[index - 1]}"
        else:
            name = f"{stage_name(stage)} ({index}/{total})"
        rounds.append(
            Round(
                number=number,
                name=name,
                stage=stage,
                first_kickoff=group[0][0],
                last_kickoff=group[-1][0],
                events={event_id: slug for _k, _s, slug, event_id in group},
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
        **SUMMARY_PLACEHOLDERS,
    }


def round_matches(
    current: Round,
    events: Iterable[tuple[str, EspnEvent]],
    local_tz: tzinfo,
) -> list[dict[str, Any]]:
    """Return the matches of ``current`` from fresh events, sorted by kickoff.

    Events outside the round are ignored; an event of the round missing from the
    answer is left out (the caller guards against an empty result).
    """
    matches: dict[str, dict[str, Any]] = {}
    for slug, event in events:
        event_id = str(event.get("id", ""))
        if event_id not in current.events or event_id in matches:
            continue
        if (match := espn_match(event, slug, local_tz)) is not None:
            matches[event_id] = match
    return sorted(matches.values(), key=lambda m: (m["matchDateTimeUTC"], m["matchID"]))

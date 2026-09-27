"""Helpers shared by the Matchday tests: recorded fixtures and data builders."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"

OLDB = "https://api.openligadb.de"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer"


def load_json(name: str) -> Any:
    """Load a recorded API answer from ``tests/fixtures`` (``.gz`` too).

    Recorded on 2026-09-26: OpenLigaDB bl1 last/next match and season 2026
    matchday 4, ESPN ger.1 scoreboard ``?dates=202609``. Recorded on 2026-09-27
    (``scripts/record_fixtures.py``): one bundle per competition in
    ``competitions/`` and three unmodified summaries ``espn_summary_*``.
    """
    path = FIXTURES / name
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


def load_bundle(competition: str) -> dict[str, Any]:
    """Load the recorded bundle of a competition (bl1, bl2, dfb, pl), 2026-09-27."""
    return load_json(f"competitions/{competition}-20260927.json.gz")


def load_history() -> dict[str, Any]:
    """Load the recorded answers behind matchday browsing, 2026-09-27.

    ``scripts/record_history.py``: OpenLigaDB groups and (slimmed) season
    fixtures of bl1, dfb and pl; bl1 matchdays 1, 2, 3, 5 and DFB-Pokal round 2;
    ESPN ger.1 scoreboards of August and September; the slimmed summary of the
    bl1 season opener.
    """
    return load_json("history/history-20260927.json.gz")


def schedule_answers(league: str, season: int = 2026) -> dict[str, Any]:
    """Return the season schedule answers (groups, season fixtures) of a league.

    Leagues without a recording (bl2, the replay competition) get empty lists:
    no ``matchdays``, no error.
    """
    history = load_history() if league in ("bl1", "dfb", "pl") else None
    return {
        f"{OLDB}/getavailablegroups/{league}/{season}": history["groups"][league]
        if history
        else [],
        f"{OLDB}/getmatchdata/{league}/{season}": history["seasons"][league]
        if history
        else [],
    }


def summary_url(slug: str, event_id: str) -> str:
    """Return the URL the coordinator requests for a match summary."""
    return f"{ESPN}/{slug}/summary?event={event_id}"


def standings_url(slug: str, season: int = 2026) -> str:
    """Return the URL the coordinator requests for the standings."""
    return f"{ESPN_STANDINGS}/{slug}/standings?season={season}"


def home_team_id(summary: dict[str, Any]) -> str:
    """Return the ESPN id of the home team of a summary."""
    competitors = summary["header"]["competitions"][0]["competitors"]
    return next(c["team"]["id"] for c in competitors if c["homeAway"] == "home")


def oldb_match(
    match_id: int,
    kickoff: str,
    team1: str,
    team2: str,
    *,
    group: int = 4,
    season: int = 2026,
    finished: bool = False,
    result: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Build a minimal OpenLigaDB match object."""
    return {
        "matchID": match_id,
        "matchDateTime": kickoff.removesuffix("Z"),
        "leagueSeason": season,
        "leagueShortcut": "bl1",
        "matchDateTimeUTC": kickoff,
        "group": {
            "groupName": f"{group}. Spieltag",
            "groupOrderID": group,
            "groupID": 50632 + group,
        },
        "team1": {"teamId": match_id * 10 + 1, "teamName": team1},
        "team2": {"teamId": match_id * 10 + 2, "teamName": team2},
        "matchIsFinished": finished,
        "matchResults": []
        if result is None
        else [
            {"resultTypeID": 1, "pointsTeam1": 0, "pointsTeam2": 0},
            {"resultTypeID": 2, "pointsTeam1": result[0], "pointsTeam2": result[1]},
        ],
    }


def espn_event(
    event_id: str,
    kickoff: str,
    home: str,
    away: str,
    *,
    state: str = "pre",
    type_name: str = "STATUS_SCHEDULED",
    score: tuple[int, int] = (0, 0),
    display_clock: str = "0'",
    details: list[dict[str, Any]] | None = None,
    home_id: str = "1",
    away_id: str = "2",
) -> dict[str, Any]:
    """Build a minimal ESPN scoreboard event (kickoff like ``2026-09-19T13:30Z``)."""
    return {
        "id": event_id,
        "date": kickoff,
        "competitions": [
            {
                "id": event_id,
                "date": kickoff,
                "status": {
                    "displayClock": display_clock,
                    "type": {"state": state, "name": type_name},
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": str(score[0]),
                        "team": {"id": home_id, "displayName": home},
                    },
                    {
                        "homeAway": "away",
                        "score": str(score[1]),
                        "team": {"id": away_id, "displayName": away},
                    },
                ],
                "details": details or [],
            }
        ],
    }


def detail(
    kind: str,
    minute: str,
    value: float,
    team_id: str,
    name: str,
    *,
    penalty: bool = False,
    own_goal: bool = False,
) -> dict[str, Any]:
    """Build an ESPN ``details`` entry; ``kind`` is goal, yellow or red."""
    return {
        "clock": {"value": value, "displayValue": minute},
        "team": {"id": team_id},
        "scoringPlay": kind == "goal",
        "yellowCard": kind == "yellow",
        "redCard": kind == "red",
        "penaltyKick": penalty,
        "ownGoal": own_goal,
        "athletesInvolved": [{"displayName": name}],
    }

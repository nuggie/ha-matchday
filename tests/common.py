"""Helpers shared by the Matchday tests: recorded fixtures and data builders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"

OLDB = "https://api.openligadb.de"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def load_json(name: str) -> Any:
    """Load a recorded API answer from ``tests/fixtures``.

    Recorded on 2026-09-26: OpenLigaDB bl1 last/next match and season 2026
    matchday 4, ESPN ger.1 scoreboard ``?dates=202609``.
    """
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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

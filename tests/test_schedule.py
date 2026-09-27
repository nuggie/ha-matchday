"""Tests for the pure helpers behind matchday browsing (logic/schedule.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .common import load_bundle, load_history, oldb_match
from custom_components.matchday.logic.schedule import (
    build_matchdays,
    is_future,
    match_settled,
    matchday_settled,
    scoreboard_months_of,
    scoreboard_pso,
)

HISTORY = load_history()
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def test_bundesliga_matchdays() -> None:
    """34 matchdays, sorted, with the kickoff range of each."""
    matchdays = build_matchdays(HISTORY["groups"]["bl1"], HISTORY["seasons"]["bl1"])
    assert [m["matchday"] for m in matchdays] == list(range(1, 35))
    assert matchdays[0] == {
        "matchday": 1,
        "name": "1. Spieltag",
        "first_kickoff": "2026-08-28T18:30:00Z",
        "last_kickoff": "2026-08-30T15:30:00Z",
    }
    assert matchdays[4]["first_kickoff"] == "2026-10-09T18:30:00Z"
    assert all(m["first_kickoff"] <= m["last_kickoff"] for m in matchdays)


def test_cup_rounds_without_fixtures() -> None:
    """Later cup rounds are listed by name, their kickoffs are unknown."""
    rounds = build_matchdays(HISTORY["groups"]["dfb"], HISTORY["seasons"]["dfb"])
    assert [(r["matchday"], r["name"]) for r in rounds] == [
        (1, "1. Runde"),
        (2, "2. Runde"),
        (3, "Achtelfinale"),
        (4, "Viertelfinale"),
        (5, "Halbfinale"),
        (6, "Endspiel"),
    ]
    assert rounds[0]["first_kickoff"] == "2026-08-21T16:00:00Z"
    assert rounds[1]["first_kickoff"] > NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert all(
        r["first_kickoff"] is None and r["last_kickoff"] is None for r in rounds[2:]
    )


def test_premier_league_matchdays() -> None:
    """The private Premier League has 38 matchdays of ten matches."""
    matchdays = build_matchdays(HISTORY["groups"]["pl"], HISTORY["seasons"]["pl"])
    assert len(matchdays) == 38
    assert matchdays[-1]["name"] == "38. Spieltag"
    assert all(m["first_kickoff"] for m in matchdays)


def test_groups_missing_in_group_list() -> None:
    """A matchday only the fixtures know is listed under their group name."""
    matches = [oldb_match(1, "2026-10-01T18:30:00Z", "A", "B", group=7)]
    assert build_matchdays([], matches) == [
        {
            "matchday": 7,
            "name": "7. Spieltag",
            "first_kickoff": "2026-10-01T18:30:00Z",
            "last_kickoff": "2026-10-01T18:30:00Z",
        }
    ]
    assert build_matchdays([{"groupOrderID": True}, {"groupOrderID": 0}], []) == []


def test_future_is_more_than_a_day_away() -> None:
    kickoff = NOW + timedelta(days=1, minutes=1)
    match = oldb_match(1, kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), "A", "B")
    assert is_future([match], NOW)
    assert not is_future([match], NOW + timedelta(minutes=2))
    assert is_future([], NOW)


def test_months_of_all_kickoffs() -> None:
    """A rescheduled match adds its month, nothing in between."""
    matches = [
        oldb_match(1, "2026-08-30T15:30:00Z", "A", "B"),
        oldb_match(2, "2026-09-01T18:30:00Z", "C", "D"),
        oldb_match(3, "2026-12-16T19:30:00Z", "E", "F"),
    ]
    assert scoreboard_months_of(matches) == ["202608", "202609", "202612"]


def _merged(kickoff: str, *, status: str = "post", espn_id: str | None = "1") -> Any:
    return {**oldb_match(1, kickoff, "A", "B"), "status": status, "espn_id": espn_id}


def test_settled_matchday() -> None:
    """All finished, paired and older than a day."""
    old = _merged("2026-09-20T17:30:00Z")
    assert match_settled(old, NOW)
    assert matchday_settled([old, old], NOW)
    assert not matchday_settled([], NOW)
    assert not matchday_settled([old, _merged("2026-09-27T09:30:00Z")], NOW)
    assert not matchday_settled(
        [old, _merged("2026-09-20T17:30:00Z", espn_id=None)], NOW
    )
    assert not matchday_settled([_merged("2026-09-20T17:30:00Z", status="live")], NOW)


def test_scoreboard_shootouts_equal_summary_format() -> None:
    """Shootouts from the cup scoreboard equal those the summaries give."""
    bundle = load_bundle("dfb")
    events = [
        event
        for scoreboard in bundle["scoreboards"].values()
        for event in scoreboard["events"]
    ]
    shootouts = scoreboard_pso(events)
    assert shootouts["401875181"] == {"team1": 2.0, "team2": 4.0, "winner": "team2"}
    assert len(shootouts) == 4
    assert all(isinstance(pso["team1"], float) for pso in shootouts.values())

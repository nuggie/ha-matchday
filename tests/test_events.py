"""Tests for the match events: kickoff, goal, goal disallowed, final whistle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from custom_components.matchday.logic.events import KEEP, EventTracker

NOW = datetime(2026, 10, 17, 13, 30, tzinfo=UTC)
CONTEXT = {"competition": "bl1", "matchday": 7}


def play(
    minute: str, name: str, team: str = "team1", kind: str = "regular"
) -> dict[str, Any]:
    """Build a ``scorers`` entry as the merge writes it."""
    base = minute.split("'")[0]
    return {
        "name": name,
        "minute": minute,
        "sortKey": int(base) * 60,
        "team": team,
        "type": kind,
    }


def match(
    status: str = "live",
    score: str | None = "0:0",
    scorers: list[dict[str, Any]] | None = None,
    *,
    minute: str | None = "10'",
    match_id: int = 1,
    espn_id: str | None = "401",
    **extra: Any,
) -> dict[str, Any]:
    """Build a match in the ``matches`` format (only what the events read)."""
    return {
        "matchID": match_id,
        "espn_id": espn_id,
        "matchDateTimeUTC": "2026-10-17T13:30:00Z",
        "team1": {"teamName": "SC Freiburg", "shortName": "SCF"},
        "team2": {"teamName": "FC Bayern München", "shortName": "FCB"},
        "status": status,
        "live_score": None if status == "pre" else score,
        "minute": minute,
        "scorers": scorers or [],
        "ht_score": None,
        "pso": None,
        **extra,
    }


class Run:
    """Feed updates one minute apart, return (kind, data) per update."""

    def __init__(self) -> None:
        self.tracker = EventTracker()
        self.now = NOW

    def __call__(self, *matches: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        self.now += timedelta(minutes=1)
        events = self.tracker.update(list(matches), self.now, CONTEXT)
        return [(event.kind, event.data) for event in events]


def kinds(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, str]]:
    return [(kind, data["score"]) for kind, data in events]


def test_first_update_only_records() -> None:
    run = Run()
    assert run(match("live", "2:0", [play("5'", "A"), play("9'", "B")])) == []
    assert run(match("live", "2:0", [play("5'", "A"), play("9'", "B")])) == []


def test_kickoff_and_its_fields() -> None:
    run = Run()
    assert run(match("pre", minute="0'")) == []
    events = run(match("live", "0:0", minute="1'"))
    assert events == [
        (
            "kickoff",
            {
                "competition": "bl1",
                "matchday": 7,
                "match_id": 1,
                "espn_id": "401",
                "team1": "SC Freiburg",
                "team2": "FC Bayern München",
                "team1_short": "SCF",
                "team2_short": "FCB",
                "score": "0:0",
                "minute": "1'",
                "status": "live",
                "kickoff": "2026-10-17T13:30:00Z",
            },
        )
    ]
    assert run(match("ht", "0:0")) == []
    assert run(match("live", "0:0")) == []


def test_goal_with_scorer() -> None:
    run = Run()
    run(match("live", "0:0", minute="66'"))
    [(kind, data)] = run(
        match("live", "0:1", [play("65'", "Kane", "team2", "penalty")], minute="67'")
    )
    assert kind == "goal"
    assert data["score"] == "0:1"
    assert data["team"] == "team2"
    assert data["team_name"] == "FC Bayern München"
    assert data["scorer"] == "Kane"
    assert data["goal_type"] == "penalty"
    assert data["minute"] == "65'"  # the goal's minute, not the clock


def test_details_ahead_of_the_score() -> None:
    """ESPN lists the goal first; the event waits for the score."""
    run = Run()
    run(match("live", "0:0"))
    assert run(match("live", "0:0", [play("12'", "Grifo")])) == []
    events = run(match("live", "1:0", [play("12'", "Grifo")]))
    assert [(k, d["score"], d["scorer"]) for k, d in events] == [
        ("goal", "1:0", "Grifo")
    ]


def test_score_ahead_of_the_details_waits_one_update() -> None:
    run = Run()
    run(match("live", "0:0"))
    assert run(match("live", "1:0")) == []
    events = run(match("live", "1:0", [play("12'", "Grifo")]))
    assert [(k, d["score"], d["scorer"]) for k, d in events] == [
        ("goal", "1:0", "Grifo")
    ]


def test_scorer_still_missing_after_one_update() -> None:
    run = Run()
    run(match("live", "0:0", minute="11'"))
    assert run(match("live", "1:0", minute="12'")) == []
    events = run(match("live", "1:0", minute="13'"))
    assert [(k, d["score"], d["scorer"], d["minute"]) for k, d in events] == [
        ("goal", "1:0", None, "13'")
    ]
    # A scorer arriving later changes nothing.
    assert run(match("live", "1:0", [play("12'", "Grifo")])) == []


def test_var_takes_the_goal_back() -> None:
    """Recorded CZE-CRO 79': score back to 1:2 first, the detail 30 s later."""
    run = Run()
    before = [play("47'", "Modric", "team2"), play("54'", "Karabec")]
    run(match("live", "1:1", before))
    var_goal = play("79'", "Radosta")
    goal = run(match("live", "2:1", [*before, var_goal]))
    assert [(k, d["scorer"]) for k, d in goal] == [("goal", "Radosta")]
    taken_back = run(match("live", "1:1", [*before, var_goal]))
    assert [
        (k, d["score"], d["team"], d["scorer"], d["minute"]) for k, d in taken_back
    ] == [("goal_disallowed", "1:1", "team1", "Radosta", "79'")]
    assert run(match("live", "1:1", before)) == []


def test_goal_taken_back_before_it_was_announced() -> None:
    run = Run()
    run(match("live", "0:0"))
    assert run(match("live", "1:0")) == []  # waits for the scorer
    assert run(match("live", "0:0")) == []


def test_last_goal_and_final_whistle_in_one_update() -> None:
    """No waiting for the scorer at the final whistle: goal first, then the end."""
    run = Run()
    run(match("live", "1:4", minute="90'+3'"))
    events = run(match("post", "1:5", minute="90'+4'", ht_score="0:1"))
    assert kinds(events) == [("goal", "1:5"), ("final_whistle", "1:5")]
    assert events[0][1]["scorer"] is None
    final = events[1][1]
    assert final["status"] == "post"
    assert final["ht_score"] == "0:1"
    assert final["pso"] is None
    assert final["aggregate"] is None


def test_final_whistle_once() -> None:
    run = Run()
    run(match("live", "1:0", [play("5'", "A")]))
    assert kinds(run(match("post", "1:0", [play("5'", "A")]))) == [
        ("final_whistle", "1:0")
    ]
    # After the final whistle: corrections and flapping announce nothing.
    assert run(match("post", "2:0")) == []
    assert run(match("live", "2:0")) == []
    assert run(match("post", "2:0")) == []


def test_running_match_reported_as_not_started_is_ignored() -> None:
    run = Run()
    run(match("live", "1:0", [play("5'", "A")]))
    assert run(match("pre")) == []
    assert run(match("live", "1:0", [play("5'", "A")])) == []


def test_match_without_espn_data_keeps_its_state() -> None:
    run = Run()
    run(match("live", "1:0", [play("5'", "A")]))
    assert run(match("pre", None, espn_id=None)) == []
    assert run(match("live", "1:0", [play("5'", "A")])) == []
    events = run(match("live", "2:0", [play("5'", "A"), play("30'", "B")]))
    assert [(k, d["score"], d["scorer"]) for k, d in events] == [("goal", "2:0", "B")]


def test_own_goal_counts_for_the_credited_side() -> None:
    run = Run()
    run(match("live", "0:0"))
    own = play("33'", "Upamecano", "team1", "owngoal")
    [(kind, data)] = run(match("live", "1:0", [own]))
    assert (kind, data["team"], data["scorer"], data["goal_type"]) == (
        "goal",
        "team1",
        "Upamecano",
        "owngoal",
    )


def test_two_goals_in_one_update_in_order() -> None:
    run = Run()
    run(match("live", "0:0"))
    plays = [play("20'", "Kane", "team2"), play("21'", "Grifo")]
    events = run(match("live", "1:1", plays))
    assert [(k, d["score"], d["scorer"]) for k, d in events] == [
        ("goal", "0:1", "Kane"),
        ("goal", "1:1", "Grifo"),
    ]


def test_kickoff_and_first_goal_in_one_update() -> None:
    run = Run()
    run(match("pre"))
    events = run(match("live", "1:0", [play("1'", "Grifo")], minute="2'"))
    assert kinds(events) == [("kickoff", "0:0"), ("goal", "1:0")]


def test_match_never_seen_running() -> None:
    run = Run()
    run(match("pre"))
    assert run(match("post", "2:1")) == []


def test_shootout_kicks_are_no_goals() -> None:
    """The score leaves the shootout out; its kicks sort after all goals."""
    run = Run()
    goals = [play("69'", "Seguin", "team2"), play("90'+2'", "Fahrner")]
    run(match("live", "1:1", goals, minute="120'"))
    kicks = [play("121'", "Brekalo", "team2", "penalty"), play("122'", "Cvjetinovic")]
    pso = {"team1": 2.0, "team2": 4.0, "winner": "team2"}
    events = run(match("post", "1:1", [*goals, *kicks], pso=pso))
    assert kinds(events) == [("final_whistle", "1:1")]
    assert events[0][1]["pso"] == pso


def test_state_of_vanished_matches_is_dropped() -> None:
    run = Run()
    run(match("live", "1:0", [play("5'", "A")]))
    run.now += KEEP
    assert run(match("live", "0:0", match_id=2)) == []
    # Match 1 is unknown again: its next update only records it.
    assert run(match("live", "2:0", [play("5'", "A"), play("80'", "B")])) == []


def test_matches_are_tracked_separately() -> None:
    run = Run()
    run(match("live", "0:0"), match("live", "0:0", match_id=2, espn_id="402"))
    events = run(
        match("live", "0:0"),
        match("live", "0:1", [play("40'", "X", "team2")], match_id=2, espn_id="402"),
    )
    assert [(k, d["match_id"], d["score"]) for k, d in events] == [("goal", 2, "0:1")]


def test_other_espn_event_is_recorded_not_announced() -> None:
    """A pairing that switches to another ESPN event brings a foreign score."""
    run = Run()
    run(match("live", "0:0"))
    assert run(match("live", "3:1", espn_id="999")) == []
    plays = [play(f"{minute}'", f"P{minute}") for minute in (10, 20, 30, 70)]
    events = run(
        match("live", "4:1", [*plays, play("50'", "Y", "team2")], espn_id="999")
    )
    assert [(k, d["score"], d["scorer"]) for k, d in events] == [("goal", "4:1", "P70")]

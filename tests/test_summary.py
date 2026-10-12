"""Tests for the ESPN summary parser and the summary fetch policy."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from .common import home_team_id, load_json, oldb_match
from .replay import Recording
from custom_components.matchday.logic.summary import (
    MAX_POST_FAILURES,
    POST_REFRESH,
    SETTLED_AFTER_KICKOFF,
    SUMMARY_REFRESH,
    SUMMARY_SETTLE,
    SummaryTracker,
    parse_summary,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "make_replay_fixture.py"
BAYERN_UNION = load_json("espn_summary_ger.1_401884790.json.gz")  # 7:0, BL 4
SAARBRUECKEN_HERTHA = load_json(  # 1:1, 2:4 on penalties, DFB-Pokal round 1
    "espn_summary_ger.dfb_pokal_401875181.json.gz"
)
CITY_SUNDERLAND = load_json("espn_summary_eng.1_401879272.json.gz")  # 5:3, PL 5
FULL_SUMMARIES = (BAYERN_UNION, SAARBRUECKEN_HERTHA, CITY_SUNDERLAND)

KICKOFF = datetime(2026, 9, 26, 18, 45, tzinfo=UTC)


@pytest.fixture(scope="module")
def fixture_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_replay_fixture", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- parser -------------------------------------------------------------------


def test_finished_bundesliga_match() -> None:
    """Bayern - Union 7:0: substitutions, half time, statistics, lineups."""
    details = parse_summary(BAYERN_UNION, home_team_id(BAYERN_UNION))

    assert details["ht_score"] == "3:0"
    assert details["pso"] is None
    subs = details["substitutions"]
    assert len(subs) == 10
    assert set(subs[0]) == {"in", "out", "minute", "sortKey", "team"}
    assert {sub["team"] for sub in subs} == {"team1", "team2"}
    assert all(sub["in"] and sub["out"] and sub["minute"] for sub in subs)
    stats = details["stats"]
    assert set(stats) == {"possession", "shots", "shots_on", "corners"}
    assert all(
        len(pair) == 2 and all(isinstance(v, int) for v in pair)
        for pair in stats.values()
    )
    assert sum(stats["possession"]) in (99, 100, 101)
    lineups = details["lineups"]
    assert len(lineups["home"]["players"]) == 11
    assert len(lineups["away"]["players"]) == 11
    assert lineups["home"]["formation"]


def test_cup_match_with_penalty_shootout() -> None:
    """Saarbrücken - Hertha 1:1, Hertha wins 4:2 on penalties."""
    details = parse_summary(SAARBRUECKEN_HERTHA, home_team_id(SAARBRUECKEN_HERTHA))
    assert details["pso"] == {"team1": 2.0, "team2": 4.0, "winner": "team2"}
    assert details["ht_score"] == "0:0"
    assert details["stats"] == {
        "possession": [37, 63],
        "shots": [7, 33],
        "shots_on": [2, 9],
        "corners": [5, 11],
    }


def test_premier_league_match() -> None:
    """The Premier League summary has the same shape."""
    details = parse_summary(CITY_SUNDERLAND, home_team_id(CITY_SUNDERLAND))
    assert details["ht_score"] is not None
    assert details["substitutions"]
    assert details["lineups"] is not None
    assert details["stats"] is not None


@pytest.mark.parametrize("summary", FULL_SUMMARIES, ids=["bl1", "dfb", "pl"])
def test_slimmed_summary_parses_the_same(
    fixture_script: ModuleType, summary: dict[str, Any]
) -> None:
    """The fixture slimming keeps everything the parser reads."""
    home = home_team_id(summary)
    assert parse_summary(fixture_script.slim_summary(summary), home) == parse_summary(
        summary, home
    )


def test_live_summaries_of_the_replay() -> None:
    """ESP @ ENG while it runs: lineups from 1', subs and half time grow in."""
    recording = Recording()
    summaries = [summary for _ts, summary in recording.summaries["401861066"]]
    parsed = [parse_summary(summary, "448") for summary in summaries]

    assert parsed[0]["lineups"]["home"]["formation"] == "4-2-3-1"
    assert parsed[0]["substitutions"] == []
    # Before half time the "half-time score" is the running first-half score.
    assert parsed[0]["ht_score"] == "0:0"
    assert parsed[-1]["ht_score"] == "2:1"
    counts = [len(p["substitutions"]) for p in parsed]
    assert counts == sorted(counts)
    assert counts[-1] == 9
    assert parsed[-1]["substitutions"][0] == {
        "in": "Mikel Oyarzabal",
        "out": "Ferran Torres",
        "minute": "55'",
        "sortKey": 3250.0,
        "team": "team2",
    }
    assert parsed[-1]["stats"] == {
        "possession": [47, 53],
        "shots": [13, 15],
        "shots_on": [5, 5],
        "corners": [6, 3],
    }


def test_empty_or_broken_summary() -> None:
    """Missing parts give "unknown" (None); substitutions are an empty list."""
    assert parse_summary({}, "1") == {
        "substitutions": [],
        "ht_score": None,
        "stats": None,
        "lineups": None,
        "pso": None,
    }
    broken = {
        "header": {"competitions": [{"competitors": [{"homeAway": "home"}]}]},
        "keyEvents": [None, {"type": {"type": "substitution"}, "participants": [{}]}],
        "boxscore": {"teams": [{"homeAway": "home", "statistics": []}]},
        "rosters": [{"homeAway": "home", "roster": []}],
    }
    assert parse_summary(broken, "1")["substitutions"] == []
    assert parse_summary(broken, "1")["ht_score"] is None
    assert parse_summary(broken, "1")["stats"] is None
    assert parse_summary(broken, "1")["lineups"] is None


def test_statistics_like_jinja() -> None:
    """Rounded to int (half to even, like Jinja), unparsable -> 0, missing -> left out."""
    summary = copy.deepcopy(BAYERN_UNION)
    teams = summary["boxscore"]["teams"]
    for team, value in zip(teams, ("52.5", "n/a"), strict=True):
        for stat in team["statistics"]:
            if stat["name"] == "possessionPct":
                stat["displayValue"] = value
            if stat["name"] == "wonCorners":
                stat["name"] = "renamed"
    stats = parse_summary(summary, home_team_id(summary))["stats"]
    assert stats["possession"] == [52, 0]
    assert "corners" not in stats


# --- fetch policy ---------------------------------------------------------------


def match(status: str, *, espn_id: str | None = "e1", match_id: int = 1) -> dict:
    """Return a merged match in ``status`` (kickoff 2026-09-26 18:45 UTC)."""
    return {
        **oldb_match(match_id, "2026-09-26T18:45:00Z", "England", "Spain"),
        "espn_id": espn_id,
        "espn_home_team_id": "448",
        "status": status,
        "substitutions": [],
        "stats": None,
        "lineups": None,
        "ht_score": None,
        "pso": None,
    }


def summary(ht: str, subs: int = 0) -> dict[str, Any]:
    """Return a minimal summary with a half-time score and ``subs`` substitutions."""
    home, away = ht.split(":")
    return {
        "header": {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "linescores": [{"displayValue": home}]},
                        {"homeAway": "away", "linescores": [{"displayValue": away}]},
                    ]
                }
            ]
        },
        "keyEvents": [
            {
                "type": {"type": "substitution"},
                "clock": {"value": 60.0 * i, "displayValue": f"{i}'"},
                "team": {"id": "448"},
                "participants": [
                    {"athlete": {"displayName": f"In {i}"}},
                    {"athlete": {"displayName": f"Out {i}"}},
                ],
            }
            for i in range(subs)
        ],
    }


def minutes(value: float) -> timedelta:
    return timedelta(minutes=value)


def test_only_started_matches_with_espn_id_are_fetched() -> None:
    """pre and unpaired matches are never fetched."""
    tracker = SummaryTracker()
    matches = [
        match("pre", match_id=1),
        match("live", espn_id=None, match_id=2),
        match("live", espn_id="e3", match_id=3),
        match("ht", espn_id="e4", match_id=4),
        match("post", espn_id="e5", match_id=5),
    ]
    due = tracker.due(matches, KICKOFF)
    assert [m["matchID"] for m in due] == [3, 4, 5]


def test_live_match_every_three_minutes_and_on_status_change() -> None:
    """60-second refreshes fetch every third time; half time right away."""
    tracker = SummaryTracker()
    now = KICKOFF + minutes(10)
    fetched = []
    for step in range(7):
        current = [match("live")]
        if tracker.due(current, now):
            tracker.store(current[0], summary("0:0"), now)
            fetched.append(step)
        # A few milliseconds of jitter must not push the fetch a minute later.
        now += timedelta(seconds=60, milliseconds=-3 if step % 2 else 3)
    assert fetched == [0, 3, 6]

    # Half time and the second half start are fetched at once.
    assert tracker.due([match("ht")], now)
    tracker.store(match("ht"), summary("1:0"), now)
    now += timedelta(seconds=60)
    assert tracker.due([match("ht")], now) == []
    assert tracker.due([match("live")], now)


def test_finished_match_is_refetched_until_settled_then_never() -> None:
    """Final whistle, every POST_REFRESH until the settle time, then final."""
    tracker = SummaryTracker()
    now = KICKOFF + minutes(100)
    assert tracker.due([match("live")], now)
    tracker.store(match("live"), summary("1:0", 3), now)

    now += minutes(1)  # final whistle seen
    whistle = now
    fetched = []
    # Window refreshes every five minutes; ESPN corrects the summary 40' later.
    while now - whistle <= SUMMARY_SETTLE + POST_REFRESH:
        if tracker.due([match("post")], now):
            subs = 5 if now - whistle < minutes(40) else 6
            tracker.store(match("post"), summary("1:0", subs), now)
            fetched.append(now - whistle)
        now += minutes(5)
    assert fetched == [
        POST_REFRESH * i for i in range(SUMMARY_SETTLE // POST_REFRESH + 1)
    ]
    assert tracker.entry(1).final
    current = [match("post")]
    tracker.apply(current)
    assert len(current[0]["substitutions"]) == 6

    for _ in range(10):
        now += minutes(30)
        assert tracker.due([match("post")], now) == []


def test_finished_long_ago_is_fetched_once() -> None:
    """After a restart a finished match costs exactly one request."""
    tracker = SummaryTracker()
    now = KICKOFF + SETTLED_AFTER_KICKOFF + minutes(1)
    assert tracker.due([match("post")], now)
    tracker.store(match("post"), summary("2:1", 9), now)
    assert tracker.entry(1).final
    assert tracker.due([match("post")], now + minutes(5)) == []


def test_details_stay_when_not_fetched() -> None:
    """The prev fallback: details survive refreshes without a fetch."""
    tracker = SummaryTracker()
    now = KICKOFF + SETTLED_AFTER_KICKOFF
    tracker.due([match("post")], now)
    tracker.store(match("post"), summary("2:1", 2), now)

    later = [match("post")]
    assert tracker.due(later, now + minutes(60)) == []
    tracker.apply(later)
    assert later[0]["ht_score"] == "2:1"
    assert len(later[0]["substitutions"]) == 2

    # Also when ESPN is not asked any more and the match lost its pairing.
    unpaired = [match("post", espn_id=None)]
    assert tracker.due(unpaired, now + minutes(90)) == []
    tracker.apply(unpaired)
    assert unpaired[0]["ht_score"] == "2:1"


def test_partial_summary_keeps_known_fields() -> None:
    """A field the new summary does not have keeps its last value."""
    tracker = SummaryTracker()
    now = KICKOFF + minutes(50)
    tracker.due([match("ht")], now)
    tracker.store(match("ht"), summary("1:0", 1), now)
    tracker.due([match("live")], now + minutes(4))
    tracker.store(match("live"), {"keyEvents": []}, now + minutes(4))

    current = [match("live")]
    tracker.apply(current)
    assert current[0]["ht_score"] == "1:0"
    # An answered summary without substitutions means none: the list follows it.
    assert current[0]["substitutions"] == []


def test_failed_fetch_keeps_details_and_is_throttled() -> None:
    """A failing summary keeps the details and is retried every three minutes."""
    tracker = SummaryTracker()
    now = KICKOFF + minutes(20)
    tracker.due([match("live")], now)
    tracker.store(match("live"), summary("0:0", 1), now)

    now += SUMMARY_REFRESH
    assert tracker.due([match("live")], now)
    tracker.store(match("live"), None, now)
    assert tracker.due([match("live")], now + minutes(1)) == []
    assert tracker.due([match("live")], now + SUMMARY_REFRESH)

    current = [match("live")]
    tracker.apply(current)
    assert len(current[0]["substitutions"]) == 1


def test_new_pairing_or_leaving_match_forgets_details() -> None:
    """Another ESPN event for the match, or a match gone from the list."""
    tracker = SummaryTracker()
    now = KICKOFF + SETTLED_AFTER_KICKOFF
    tracker.due([match("post"), match("post", espn_id="e2", match_id=2)], now)
    tracker.store(match("post"), summary("1:1"), now)
    tracker.store(match("post", espn_id="e2", match_id=2), summary("0:0"), now)

    assert tracker.due(
        [match("post", espn_id="other"), match("post", espn_id="e2", match_id=2)], now
    )
    assert tracker.entry(1).details == {}
    tracker.due([match("post", espn_id="other")], now)
    assert tracker.entry(2) is None


def test_finished_match_gives_up_after_failures() -> None:
    """A finished match without summary is not asked forever."""
    tracker = SummaryTracker()
    now = KICKOFF + SETTLED_AFTER_KICKOFF
    attempts = 0
    for _ in range(10):
        if tracker.due([match("post")], now):
            tracker.store(match("post"), None, now)
            attempts += 1
        now += SUMMARY_REFRESH
    assert attempts == MAX_POST_FAILURES

    # A running match keeps trying.
    tracker = SummaryTracker()
    attempts = 0
    for _ in range(10):
        if tracker.due([match("live")], now):
            tracker.store(match("live"), None, now)
            attempts += 1
        now += SUMMARY_REFRESH
    assert attempts == 10

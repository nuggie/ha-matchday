"""Tests for the league table: standings + OpenLigaDB names, live delta, post gap."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from .common import load_bundle
from custom_components.matchday.logic.merge import NORMALIZERS, merge_matches
from custom_components.matchday.logic.table import (
    PENDING_TIMEOUT,
    STANDINGS_ACTIVE,
    STANDINGS_IDLE,
    STANDINGS_PENDING,
    TableTracker,
    build_table,
    live_table,
    norm_table,
    standings_interval,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
TABLE_KEYS = {
    "espn_team_id",
    "teamName",
    "shortName",
    "teamIconUrl",
    "matches",
    "won",
    "draw",
    "lost",
    "goals",
    "opponentGoals",
    "diff",
    "points",
}


def entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return bundle["standings"]["children"][0]["standings"]["entries"]


def merged(key: str, names: str = "de") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the bundle of ``key`` and its matchday merged like the coordinator."""
    bundle = load_bundle(key)
    events: dict[str, Any] = {}
    for scoreboard in bundle["scoreboards"].values():
        for event in scoreboard["events"]:
            events.setdefault(event["id"], event)
    matches = merge_matches(
        bundle["matches"],
        list(events.values()),
        NORMALIZERS[names],
        ZoneInfo("Europe/Berlin"),
    )
    return bundle, matches


# --- names ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("espn", "oldb"),
    [
        ("Bayern Munich", "FC Bayern München"),
        ("FC Cologne", "1. FC Köln"),
        ("Borussia Mönchengladbach", "Borussia Mönchengladbach"),
        ("VfL Wolfsburg", "Wolfsburg"),
        ("Brighton & Hove Albion", "Brighton and Hove Albion"),
    ],
)
def test_norm_table(espn: str, oldb: str) -> None:
    """The table normalization of the Jinja blocks."""
    assert norm_table(espn) == norm_table(oldb)


@pytest.mark.parametrize(
    ("key", "names", "teams"),
    [("bl1", "de", 18), ("bl2", "de", 18), ("pl", "en", 20)],
)
def test_recorded_tables_use_openligadb_names(key: str, names: str, teams: int) -> None:
    """Every ESPN team gets its OpenLigaDB name, short name and crest.

    Checked against the template sensors on 2026-09-27: identical in all fields.
    """
    bundle, matches = merged(key, names)
    table = build_table(entries(bundle), bundle["oldb_table"], matches)

    assert len(table) == teams
    assert all(set(row) == TABLE_KEYS for row in table)
    oldb = {team["teamName"]: team for team in bundle["oldb_table"]}
    for row in table:
        assert row["teamName"] in oldb, row
        assert row["shortName"] == oldb[row["teamName"]]["shortName"]
        assert row["teamIconUrl"] == oldb[row["teamName"]]["teamIconUrl"]
        assert row["diff"] == row["goals"] - row["opponentGoals"]
        assert row["matches"] == row["won"] + row["draw"] + row["lost"]
    # ESPN's order (rank) is kept.
    ranks = [
        next(s["value"] for s in entry["stats"] if s["name"] == "rank")
        for entry in entries(bundle)
    ]
    assert ranks == sorted(ranks)


def test_bundesliga_top_of_the_table() -> None:
    """Spot check after matchday 4."""
    bundle, matches = merged("bl1")
    table = build_table(entries(bundle), bundle["oldb_table"], matches)
    assert table[0] == {
        "espn_team_id": "124",
        "teamName": "Borussia Dortmund",
        "shortName": "Dortmund",
        "teamIconUrl": table[0]["teamIconUrl"],
        "matches": 4,
        "won": 4,
        "draw": 0,
        "lost": 0,
        "goals": 9,
        "opponentGoals": 2,
        "diff": 7,
        "points": 12,
    }
    # "Borussia Mönchengladbach": matchday pairing, not the name.
    assert table[-1]["teamName"] == "Borussia Mönchengladbach"


def team_entry(team_id: str, name: str, **stats: float) -> dict[str, Any]:
    return {
        "team": {
            "id": team_id,
            "displayName": name,
            "abbreviation": name[:3].upper(),
            "logos": [{"href": f"https://espn/{team_id}.png"}],
        },
        "stats": [{"name": key, "value": value} for key, value in stats.items()],
    }


def test_name_sources_in_order() -> None:
    """Matchday pairing, name, word subset, previous table, ESPN itself."""
    standings = [
        team_entry("1", "Bayern Munich", gamesPlayed=1, points=3),
        team_entry("2", "Hertha Berlin"),
        team_entry("3", "Arminia Bielefeld"),
        team_entry("4", "Unknown Town"),
        team_entry("5", "Nowhere United"),
    ]
    oldb = [
        {"teamName": "FC Bayern München", "shortName": "Bayern", "teamIconUrl": "b"},
        {"teamName": "Hertha BSC", "shortName": "Hertha", "teamIconUrl": "h"},
        {
            "teamName": "DSC Arminia Bielefeld",
            "shortName": "Arminia",
            "teamIconUrl": "a",
        },
    ]
    matches = [
        {
            "espn_home_team_id": "1",
            "espn_away_team_id": "9",
            "team1": {
                "teamName": "Bayern (Spieltag)",
                "shortName": "FCB",
                "teamIconUrl": "m",
            },
            "team2": {},
        }
    ]
    previous = [{"espn_team_id": "4", "teamName": "Früher", "shortName": "FRÜ"}]

    table = build_table(standings, oldb, matches, previous)
    names = [(row["teamName"], row["shortName"], row["teamIconUrl"]) for row in table]
    assert names == [
        ("Bayern (Spieltag)", "FCB", "m"),
        ("Hertha BSC", "Hertha", "h"),
        ("DSC Arminia Bielefeld", "Arminia", "a"),
        ("Früher", "FRÜ", "https://espn/4.png"),
        ("Nowhere United", "NOW", "https://espn/5.png"),
    ]
    assert (table[0]["matches"], table[0]["points"]) == (1, 3)


def test_ambiguous_word_subset_keeps_espn_name() -> None:
    """Two OpenLigaDB candidates: better the ESPN name than a wrong one."""
    standings = [team_entry("1", "Berlin")]
    oldb = [{"teamName": "Hertha Berlin"}, {"teamName": "Union Berlin"}]
    assert build_table(standings, oldb, [])[0]["teamName"] == "Berlin"


# --- live delta -----------------------------------------------------------------


def base_table() -> list[dict[str, Any]]:
    """Four teams, ESPN order A, B, C, D."""
    rows = [
        ("a", 9, 10, 4),
        ("b", 9, 8, 4),
        ("c", 7, 6, 5),
        ("d", 7, 6, 5),
    ]
    return [
        {
            "espn_team_id": team,
            "teamName": team.upper(),
            "matches": 3,
            "won": points // 3,
            "draw": points % 3,
            "lost": 3 - points // 3 - points % 3,
            "goals": goals,
            "opponentGoals": against,
            "diff": goals - against,
            "points": points,
        }
        for team, points, goals, against in rows
    ]


def game(
    match_id: int, home: str, away: str, score: str | None, status: str = "live"
) -> dict[str, Any]:
    return {
        "matchID": match_id,
        "matchDateTimeUTC": "2026-09-27T10:30:00Z",
        "espn_home_team_id": home,
        "espn_away_team_id": away,
        "live_score": score,
        "status": status,
    }


def both(match: dict[str, Any]) -> tuple[dict[str, Any], frozenset[str]]:
    return match, frozenset({match["espn_home_team_id"], match["espn_away_team_id"]})


def test_live_delta_and_sorting() -> None:
    """Running matches are added and the table re-sorted by points, diff, goals."""
    table = live_table(
        base_table(), [both(game(1, "c", "a", "2:0")), both(game(2, "b", "d", "1:1"))]
    )
    assert [row["espn_team_id"] for row in table] == ["b", "c", "a", "d"]
    c = table[1]
    assert (c["matches"], c["won"], c["points"], c["goals"], c["diff"]) == (
        4,
        3,
        10,
        8,
        3,
    )
    assert (c["live"], c["live_score"]) == (True, "2:0")
    a = table[2]
    assert (a["lost"], a["live_score"], a["opponentGoals"]) == (1, "0:2", 6)


def test_ties_keep_espn_order() -> None:
    """Same points, diff and goals: the order of the standings stays."""
    table = live_table(base_table(), [])
    assert [row["espn_team_id"] for row in table] == ["a", "b", "c", "d"]
    assert not any(row["live"] for row in table)
    assert all(row["live_score"] is None for row in table)


def test_match_without_score_is_ignored() -> None:
    """Like Jinja: no live_score, no delta."""
    table = live_table(base_table(), [both(game(1, "c", "a", None))])
    assert [row["matches"] for row in table] == [3, 3, 3, 3]


# --- the gap after the final whistle -------------------------------------------


def booked(table: list[dict[str, Any]], *teams: str, score: str) -> list[dict]:
    """Return ``table`` with the match of ``teams`` booked by ESPN."""
    table = copy.deepcopy(table)
    home, away = (int(goals) for goals in score.split(":"))
    for row in table:
        if row["espn_team_id"] in teams:
            scored, conceded = (
                (home, away) if row["espn_team_id"] == teams[0] else (away, home)
            )
            row["matches"] += 1
            row["goals"] += scored
            row["opponentGoals"] += conceded
            row["diff"] = row["goals"] - row["opponentGoals"]
            row["points"] += 3 if scored > conceded else 1 if scored == conceded else 0
    return table


def test_finished_match_counts_until_espn_books_it() -> None:
    """live -> post: still counted with the unchanged standings; booked -> dropped."""
    tracker = TableTracker()
    base = base_table()
    running = game(1, "c", "a", "2:0")

    counted = tracker.counted([running], base, NOW)
    assert live_table(base, counted)[1]["matches"] == 4

    finished = game(1, "c", "a", "2:0", status="post")
    counted = tracker.counted([finished], base, NOW + timedelta(minutes=1))
    assert tracker.has_pending(counted)
    table = live_table(base, counted)
    c = next(row for row in table if row["espn_team_id"] == "c")
    assert (c["matches"], c["points"], c["live"], c["live_score"]) == (
        4,
        10,
        False,
        None,
    )

    after = booked(base, "c", "a", score="2:0")
    counted = tracker.counted([finished], after, NOW + timedelta(minutes=6))
    assert counted == []
    assert not tracker.has_pending(counted)
    # Same numbers as with the delta, now from the standings alone.
    c = next(row for row in live_table(after, counted) if row["espn_team_id"] == "c")
    assert (c["matches"], c["points"], c["goals"]) == (4, 10, 8)


def test_teams_are_booked_one_by_one() -> None:
    """If ESPN shows one team updated first, only the other one still counts."""
    tracker = TableTracker()
    base = base_table()
    tracker.counted([game(1, "c", "a", "1:1")], base, NOW)
    half_booked = copy.deepcopy(base)
    half_booked[2]["matches"] += 1  # team c
    counted = tracker.counted(
        [game(1, "c", "a", "1:1", status="post")], half_booked, NOW
    )
    assert [teams for _match, teams in counted] == [frozenset({"a"})]
    table = {row["espn_team_id"]: row for row in live_table(half_booked, counted)}
    assert table["a"]["matches"] == 4
    assert table["c"]["matches"] == 4


def test_without_snapshot_a_finished_match_is_not_counted() -> None:
    """After a restart the gap is the smaller error than counting twice."""
    tracker = TableTracker()
    counted = tracker.counted(
        [game(1, "c", "a", "2:0", status="post")], base_table(), NOW
    )
    assert counted == []


def test_pending_match_times_out() -> None:
    """A match ESPN never books stops counting after the timeout."""
    tracker = TableTracker()
    base = base_table()
    tracker.counted([game(1, "c", "a", "2:0")], base, NOW)
    finished = [game(1, "c", "a", "2:0", status="post")]
    assert tracker.counted(finished, base, NOW + timedelta(minutes=2))
    assert tracker.counted(finished, base, NOW + PENDING_TIMEOUT)
    assert (
        tracker.counted(finished, base, NOW + timedelta(minutes=2) + PENDING_TIMEOUT)
        == []
    )


def test_snapshot_waits_for_standings_and_forgets_old_matches() -> None:
    """No standings yet: counted, snapshot later; a match that left is forgotten."""
    tracker = TableTracker()
    running = game(1, "c", "a", "0:0")
    assert tracker.counted([running], None, NOW) == [both(running)]
    tracker.counted([running], base_table(), NOW)
    finished = game(1, "c", "a", "0:0", status="post")
    assert tracker.counted([finished], base_table(), NOW)
    tracker.counted([], base_table(), NOW)
    assert tracker.counted([finished], base_table(), NOW) == []


def test_unpaired_match_is_skipped() -> None:
    """Without ESPN team ids nothing can be counted."""
    tracker = TableTracker()
    match = game(1, "c", "a", "1:0")
    match["espn_home_team_id"] = None
    assert tracker.counted([match], base_table(), NOW) == []


@pytest.mark.parametrize(
    ("statuses", "pending", "age", "expected"),
    [
        (["pre"], False, timedelta(hours=-2), STANDINGS_IDLE),
        (["live"], False, timedelta(minutes=30), STANDINGS_ACTIVE),
        (["post"], False, timedelta(hours=2), STANDINGS_ACTIVE),
        (["post"], False, timedelta(hours=6), STANDINGS_IDLE),
        (["post"], True, timedelta(hours=2), STANDINGS_PENDING),
    ],
)
def test_standings_interval(
    statuses: list[str], pending: bool, age: timedelta, expected: timedelta
) -> None:
    """5 min while a match waits for booking, 10 around matches, 30 otherwise."""
    kickoff = (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    matches = [
        {**game(i, "c", "a", "0:0", status), "matchDateTimeUTC": kickoff}
        for i, status in enumerate(statuses)
    ]
    assert standings_interval(matches, pending, NOW) == expected

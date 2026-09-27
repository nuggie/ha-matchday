"""Tests for pairing OpenLigaDB matches with ESPN events and the enrichment."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from .common import detail, espn_event, load_json, oldb_match
from custom_components.matchday.logic.merge import (
    SUMMARY_PLACEHOLDERS,
    extract_details,
    match_status,
    merge_matches,
    minute_sort_key,
    norm_de,
    norm_en,
    pair_matches,
)

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.mark.parametrize(
    ("oldb", "espn"),
    [
        ("1. FSV Mainz 05", "Mainz"),
        ("Hamburger SV", "Hamburg SV"),
        ("FC Bayern München", "Bayern Munich"),
        ("1. FC Köln", "FC Cologne"),
        ("Bayer 04 Leverkusen", "Bayer Leverkusen"),
        ("SV 07 Elversberg", "SV Elversberg"),
        ("Borussia Mönchengladbach", "Borussia Mönchengladbach"),
        ("TSG Hoffenheim", "TSG Hoffenheim"),
    ],
)
def test_norm_de_matches_espn_spelling(oldb: str, espn: str) -> None:
    """German OpenLigaDB names and ESPN names normalize to the same key."""
    assert norm_de(oldb) == norm_de(espn)


def test_norm_de_details() -> None:
    """Words are sorted, club-form words and digits dropped."""
    assert norm_de("SV Werder Bremen") == "bremen werder"
    assert norm_de("RB Leipzig") == "leipzig"
    assert norm_de("") == ""


@pytest.mark.parametrize(
    ("oldb", "espn"),
    [
        ("Brighton & Hove Albion", "Brighton and Hove Albion"),
        ("Brentford F.C.", "Brentford"),
        ("Tottenham Hotspur FC", "Tottenham Hotspur"),
    ],
)
def test_norm_en(oldb: str, espn: str) -> None:
    """The Premier League normalization of the Jinja block."""
    assert norm_en(oldb) == norm_en(espn)


def test_recorded_matchday_merges_completely() -> None:
    """All nine matches of Bundesliga matchday 4 pair with their ESPN events."""
    oldb = load_json("oldb_bl1_2026_4.json")
    events = load_json("espn_ger1_202609.json")["events"]
    merged = merge_matches(oldb, events, norm_de, BERLIN)

    assert len(merged) == 9
    assert all(match["status"] == "post" for match in merged)
    by_id = {match["matchID"]: match for match in merged}

    bayern = by_id[83183]
    assert bayern["espn_id"] == "401884790"
    assert bayern["espn_home_team_id"] == "132"
    assert bayern["espn_away_team_id"] == "598"
    assert bayern["live_score"] == "7:0"
    assert bayern["oldb_score"] == "7:0"
    assert bayern["minute"] == "90'+3'"
    assert bayern["matchDateTimeUTC"] == "2026-09-18T18:30:00Z"
    assert bayern["matchDateTime"] == "2026-09-18T20:30:00"
    assert [s["name"] for s in bayern["scorers"]] == [
        "Jamal Musiala",
        "Harry Kane",
        "Michael Olise",
        "Harry Kane",
        "Ismael Saibari",
        "Michael Olise",
        "Michael Olise",
    ]
    assert bayern["scorers"][1]["type"] == "penalty"
    assert {s["team"] for s in bayern["scorers"]} == {"team1"}
    assert [(c["name"], c["minute"], c["team"]) for c in bayern["cards"]][2] == (
        "Dayot Upamecano",
        "45'+2'",
        "team1",
    )
    # OpenLigaDB fields stay untouched, summary fields are placeholders.
    assert bayern["group"]["groupName"] == "4. Spieltag"
    assert bayern["team1"]["teamName"] == "FC Bayern München"
    for key, value in SUMMARY_PLACEHOLDERS.items():
        assert bayern[key] == value

    gladbach = by_id[83187]
    assert gladbach["live_score"] == "3:4"
    stoppage = [c["minute"] for c in gladbach["cards"] if c["minute"].startswith("90")]
    assert stoppage == ["90'+3'", "90'+3'", "90'+4'", "90'+7'"]
    assert gladbach["scorers"][-1]["minute"] == "90'+5'"


def test_stage_one_needs_names_and_minute() -> None:
    """Stage 1: equal kickoff minute and equal normalized names."""
    oldb = [oldb_match(1, "2026-09-19T13:30:00Z", "Hamburger SV", "1. FC Köln")]
    events = [
        espn_event("x", "2026-09-19T13:30Z", "SV Werder Bremen", "FC Augsburg"),
        espn_event("y", "2026-09-19T13:30Z", "Hamburg SV", "FC Cologne"),
    ]
    assert {i: e.id for i, e in pair_matches(oldb, events, norm_de).items()} == {0: "y"}


def test_stage_two_pairs_rescheduled_match_and_takes_espn_kickoff() -> None:
    """Stage 2: same pairing moved by a day; ESPN's kickoff wins and resorts."""
    oldb = [
        oldb_match(1, "2026-09-19T13:30:00Z", "Hamburger SV", "1. FC Köln"),
        oldb_match(2, "2026-09-19T16:30:00Z", "VfB Stuttgart", "Borussia Dortmund"),
    ]
    events = [
        espn_event(
            "moved",
            "2026-09-20T15:30Z",
            "Hamburg SV",
            "FC Cologne",
            state="post",
            type_name="STATUS_FULL_TIME",
            score=(2, 1),
        ),
        espn_event("stu", "2026-09-19T16:30Z", "VfB Stuttgart", "Borussia Dortmund"),
    ]
    merged = merge_matches(oldb, events, norm_de, BERLIN)

    assert [m["matchID"] for m in merged] == [2, 1]
    moved = merged[1]
    assert moved["espn_id"] == "moved"
    assert moved["matchDateTimeUTC"] == "2026-09-20T15:30:00Z"
    assert moved["matchDateTime"] == "2026-09-20T17:30:00"
    assert moved["live_score"] == "2:1"


def test_stage_two_tolerance_is_four_days() -> None:
    """A pairing five days away is a different match, not a reschedule."""
    oldb = [oldb_match(1, "2026-09-19T13:30:00Z", "Hamburger SV", "1. FC Köln")]
    within = [espn_event("a", "2026-09-23T13:30Z", "Hamburg SV", "FC Cologne")]
    beyond = [espn_event("b", "2026-09-24T13:30Z", "Hamburg SV", "FC Cologne")]
    assert pair_matches(oldb, within, norm_de)[0].id == "a"
    assert pair_matches(oldb, beyond, norm_de) == {}


def test_stage_three_unique_slot_fallback() -> None:
    """Stage 3: names too different, but the slot holds one match on each side."""
    oldb = [
        oldb_match(1, "2026-09-19T13:30:00Z", "Hamburger SV", "1. FC Köln"),
        oldb_match(
            2, "2026-09-19T16:30:00Z", "Borussia Mönchengladbach", "1. FSV Mainz 05"
        ),
    ]
    events = [
        espn_event("hsv", "2026-09-19T13:30Z", "Hamburg SV", "FC Cologne"),
        espn_event("bmg", "2026-09-19T16:30Z", "Gladbach", "Mainz"),
    ]
    assert norm_de("Borussia Mönchengladbach") != norm_de("Gladbach")
    pairs = pair_matches(oldb, events, norm_de)
    assert {i: e.id for i, e in pairs.items()} == {0: "hsv", 1: "bmg"}


def test_stage_three_skips_ambiguous_slots() -> None:
    """Two open matches in the same slot: no guessing."""
    oldb = [
        oldb_match(1, "2026-09-19T13:30:00Z", "Alpha", "Beta"),
        oldb_match(2, "2026-09-19T13:30:00Z", "Gamma", "Delta"),
    ]
    events = [
        espn_event("x", "2026-09-19T13:30Z", "Omega", "Sigma"),
        espn_event("y", "2026-09-19T13:30Z", "Kappa", "Lambda"),
    ]
    assert pair_matches(oldb, events, norm_de) == {}


def test_no_event_is_used_twice() -> None:
    """An event claimed in stage 1 is not available to later stages."""
    oldb = [
        oldb_match(1, "2026-09-19T13:30:00Z", "Hamburger SV", "1. FC Köln"),
        # Same pairing again (e.g. a data error): must not grab the same event.
        oldb_match(2, "2026-09-20T13:30:00Z", "Hamburger SV", "1. FC Köln"),
    ]
    events = [espn_event("only", "2026-09-19T13:30Z", "Hamburg SV", "FC Cologne")]
    pairs = pair_matches(oldb, events, norm_de)
    assert {i: e.id for i, e in pairs.items()} == {0: "only"}


@pytest.mark.parametrize(
    ("finished", "state", "type_name", "expected"),
    [
        (False, "pre", "STATUS_SCHEDULED", "pre"),
        (False, "in", "STATUS_FIRST_HALF", "live"),
        (False, "in", "STATUS_HALFTIME", "ht"),
        (False, "in", "STATUS_SECOND_HALF", "live"),
        (False, "post", "STATUS_FULL_TIME", "post"),
        (True, "pre", "STATUS_SCHEDULED", "post"),
        (True, "in", "STATUS_HALFTIME", "post"),
        (False, "", "", "pre"),
    ],
)
def test_match_status(
    finished: bool, state: str, type_name: str, expected: str
) -> None:
    """OpenLigaDB finished or ESPN post wins; half time is its own status."""
    assert match_status(finished, state, type_name) == expected


def test_live_score_only_once_the_match_started() -> None:
    """``live_score`` is set for live, ht and post, not before kickoff."""
    oldb = [
        oldb_match(1, "2026-09-19T13:30:00Z", "Hamburger SV", "1. FC Köln"),
        oldb_match(2, "2026-09-19T16:30:00Z", "VfB Stuttgart", "Borussia Dortmund"),
    ]
    events = [
        espn_event(
            "a",
            "2026-09-19T13:30Z",
            "Hamburg SV",
            "FC Cologne",
            state="in",
            type_name="STATUS_HALFTIME",
            score=(1, 0),
            display_clock="45'+2'",
        ),
        espn_event("b", "2026-09-19T16:30Z", "VfB Stuttgart", "Borussia Dortmund"),
    ]
    first, second = merge_matches(oldb, events, norm_de, BERLIN)
    assert (first["status"], first["live_score"], first["minute"]) == (
        "ht",
        "1:0",
        "45'+2'",
    )
    assert (second["status"], second["live_score"]) == ("pre", None)


def test_unpaired_match_keeps_openligadb_data() -> None:
    """Without an ESPN event the status and score come from OpenLigaDB."""
    oldb = [
        oldb_match(1, "2026-09-19T13:30:00Z", "A", "B", finished=True, result=(2, 2)),
        oldb_match(2, "2026-09-19T16:30:00Z", "C", "D"),
    ]
    finished, upcoming = merge_matches(oldb, [], norm_de, BERLIN)
    assert finished["status"] == "post"
    assert finished["oldb_score"] == "2:2"
    assert finished["espn_id"] is None
    assert finished["live_score"] is None
    assert finished["scorers"] == [] and finished["cards"] == []
    assert finished["matchDateTimeUTC"] == "2026-09-19T13:30:00Z"
    assert upcoming["status"] == "pre"
    assert upcoming["oldb_score"] is None


def test_oldb_score_prefers_extra_time_result() -> None:
    """OpenLigaDB keeps "Endergebnis" at the 90-minute score; extra time is type 4.

    Real case, DFB-Pokal 2026 round 1: Mannheim - Kaiserslautern 0:0 after 90
    minutes, 0:1 after extra time. The shoot-out (type 5) must not be used.
    """
    match = oldb_match(
        1, "2026-08-21T16:00:00Z", "Mannheim", "Kaiserslautern", finished=True
    )
    match["matchResults"] = [
        {"resultTypeID": 1, "pointsTeam1": 0, "pointsTeam2": 0},
        {"resultTypeID": 2, "pointsTeam1": 0, "pointsTeam2": 0},
        {"resultTypeID": 4, "pointsTeam1": 0, "pointsTeam2": 1},
    ]
    shootout = oldb_match(
        2, "2026-08-21T16:00:00Z", "Altglienicke", "Wolfsburg", finished=True
    )
    shootout["matchResults"] = [
        {"resultTypeID": 2, "pointsTeam1": 2, "pointsTeam2": 2},
        {"resultTypeID": 4, "pointsTeam1": 3, "pointsTeam2": 3},
        {"resultTypeID": 5, "pointsTeam1": 8, "pointsTeam2": 9},
    ]
    extra_time, penalties = merge_matches([match, shootout], [], norm_de, BERLIN)
    assert extra_time["oldb_score"] == "0:1"
    assert penalties["oldb_score"] == "3:3"


def test_details_team_types_and_stoppage_time_order() -> None:
    """Goal types, card colours, team by ESPN home id, stoppage time in order."""
    competition = espn_event(
        "e",
        "2026-09-19T13:30Z",
        "Home",
        "Away",
        home_id="10",
        away_id="20",
        details=[
            detail("goal", "12'", 700, "10", "Early"),
            # clock.value is capped at 2700/5400 in stoppage time
            detail("yellow", "45'+2'", 2700, "20", "Booked"),
            detail("goal", "90'+5'", 5400, "20", "Late", penalty=True),
            detail("goal", "90'+2'", 5400, "10", "Earlier", own_goal=True),
            detail("red", "46'", 2760, "10", "Sent off"),
        ],
    )["competitions"][0]
    scorers, cards = extract_details(competition, "10")

    assert [(s["name"], s["type"], s["team"]) for s in scorers] == [
        ("Early", "regular", "team1"),
        ("Earlier", "owngoal", "team1"),
        ("Late", "penalty", "team2"),
    ]
    assert [(c["name"], c["type"], c["team"]) for c in cards] == [
        ("Booked", "yellow", "team2"),
        ("Sent off", "red", "team1"),
    ]
    assert scorers[2] == {
        "name": "Late",
        "minute": "90'+5'",
        "sortKey": 5400,
        "type": "penalty",
        "team": "team2",
    }


@pytest.mark.parametrize(
    ("display", "value", "key"),
    [
        ("18'", 1025.0, (18, 0, 1025.0)),
        ("45'+2'", 2700.0, (45, 2, 2700.0)),
        ("90'+12'", 5400.0, (90, 12, 5400.0)),
        ("", 1025.0, (18, 0, 1025.0)),
    ],
)
def test_minute_sort_key(
    display: str, value: float, key: tuple[int, int, float]
) -> None:
    """The displayed minute sorts; the capped clock value only breaks ties."""
    assert minute_sort_key(display, value) == key
    assert minute_sort_key("90'+2'", 5400) < minute_sort_key("90'+5'", 5400)
    assert minute_sort_key("45'+3'", 2700) < minute_sort_key("46'", 2710)

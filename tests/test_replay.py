"""Replay of a recorded live evening (UEFA Nations League, 2026-09-26).

The coordinator runs against real ESPN scoreboards of three games recorded every
30 seconds: SCO @ SVN (0:0, 15:00 local), ESP @ ENG (five goals, lead changes,
90'+6') and CRO @ CZE (a goal taken back by VAR), both 20:45 local. A synthetic
OpenLigaDB season makes them matchday 3; the clock runs in the steps the
coordinator chooses itself (see ``tests/replay.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant

from .common import oldb_match
from .replay import (
    MATCHDAY,
    NEXT_MATCH,
    OLDB_RESULT_DELAY,
    REPLAY_COMPETITION,
    REPLAY_MATCHES,
    FiredEvent,
    Observation,
    Recording,
    Replay,
    collapse,
    competition_of,
    espn_score,
    espn_status,
    parse_ts,
)
from custom_components.matchday.const import COMPETITIONS
from custom_components.matchday.logic.matchday import (
    INTERVAL_IDLE,
    INTERVAL_LIVE,
    INTERVAL_WINDOW,
    KICKOFF_LEAD,
)
from custom_components.matchday.logic.merge import norm_de, norm_en, pair_matches
from custom_components.matchday.logic.summary import (
    LINEUP_GRACE,
    LINEUP_LEAD,
    MAX_POST_FAILURES,
    POST_REFRESH,
    PRE_REFRESH,
    SUMMARY_REFRESH,
    SUMMARY_SETTLE,
)

RECORDING = Recording()
GAMES = ("SCO @ SVN", "CRO @ CZE", "ESP @ ENG")
EARLY, VAR, LEAD_CHANGES = GAMES
EVENT_IDS = {EARLY: "401861057", VAR: "401861063", LEAD_CHANGES: "401861066"}

# 23:00 local on the Thursday before: the ESPN window opens at local midnight.
START = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
# Local midnight after the second day past the matchday: the window closes.
WINDOW_CLOSES = datetime(2026, 9, 28, 22, 0, tzinfo=UTC)
END = WINDOW_CLOSES + timedelta(minutes=30)

KICKOFF = {name: parse_ts(kickoff) for name, _h, _a, kickoff in REPLAY_MATCHES.values()}
LIVE_STATUSES = ("live", "ht")

_TIMELINE: list[Observation] = []


def utc(hour: int, minute: int, second: int = 0, day: int = 26) -> datetime:
    """Return 2026-09-``day`` ``hour``:``minute``:``second`` UTC."""
    return datetime(2026, 9, day, hour, minute, second, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def replay_competition(hass: HomeAssistant) -> AsyncIterator[None]:
    """Make the test competition known (only here) and use local time Berlin."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    with patch.dict(COMPETITIONS, {REPLAY_COMPETITION.key: REPLAY_COMPETITION}):
        yield


@pytest.fixture
def replay(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> Replay:
    """Return a replay of the recording, not yet set up."""
    return Replay(hass, aioclient_mock, freezer, RECORDING)


@pytest.fixture
async def timeline(replay: Replay) -> list[Observation]:
    """Replay the whole recording from Thursday to Monday night.

    About 1400 refreshes in a few seconds; the observations are plain data, so the
    run is shared by the tests of this module instead of repeated.
    """
    if not _TIMELINE:
        await replay.async_setup(START)
        _TIMELINE.extend(await replay.async_run_until(END, RECORDING.changes()))
    return _TIMELINE


def live(observation: Observation) -> bool:
    """Return True if a match is running in ``observation``."""
    return any(match.status in LIVE_STATUSES for match in observation.matches.values())


def expected_status(event: dict[str, Any]) -> str:
    """Return the status a recorded ESPN event must map to (no OpenLigaDB flag)."""
    state, name = espn_status(event)
    if state == "in":
        return "ht" if name == "STATUS_HALFTIME" else "live"
    return state


def detail_entries(event: dict[str, Any], scoring: bool) -> list[tuple[str, str, str]]:
    """Return (minute, name, team) of the scoring plays or cards, in ESPN order."""
    competition = competition_of(event)
    home_id = next(
        c["team"]["id"] for c in competition["competitors"] if c["homeAway"] == "home"
    )
    return [
        (
            detail["clock"]["displayValue"],
            detail["athletesInvolved"][0]["displayName"],
            "team1" if detail["team"]["id"] == home_id else "team2",
        )
        for detail in competition.get("details") or []
        if bool(detail.get("scoringPlay")) == scoring
    ]


# --- pairing ------------------------------------------------------------------


def test_english_names_pair_by_kickoff_and_name() -> None:
    """ESPN's national team names pair in stage 1 with the "en" normalizer.

    Both 18:45 games share a time slot, so the slot fallback (stage 4) could not
    tell them apart: the pairing below is by name. The "de" normalizer gives the
    same result for these English names. German OpenLigaDB names ("Tschechien")
    pair only because "England" is spelled alike: stage 3 takes England - Spain
    by one name, then Czechia - Croatia is alone in the slot for stage 4.
    """
    snapshot = RECORDING.at(utc(20, 0))
    oldb = [
        oldb_match(match_id, kickoff, home, away, group=MATCHDAY)
        for match_id, (_name, home, away, kickoff) in REPLAY_MATCHES.items()
    ]
    events = list(snapshot.events)

    paired = pair_matches(oldb, events, norm_en)
    assert {oldb[i]["matchID"]: e.id for i, e in paired.items()} == {
        3001: "401861057",
        3002: "401861063",
        3003: "401861066",
    }
    assert pair_matches(oldb, events, norm_de).keys() == paired.keys()

    german = [
        oldb_match(3001, "2026-09-26T13:00:00Z", "Slowenien", "Schottland"),
        oldb_match(3002, "2026-09-26T18:45:00Z", "Tschechien", "Kroatien"),
        oldb_match(3003, "2026-09-26T18:45:00Z", "England", "Spanien"),
    ]
    by_one_name = pair_matches(german, events, norm_de)
    assert {german[i]["matchID"]: e.id for i, e in by_one_name.items()} == {
        3001: "401861057",
        3002: "401861063",
        3003: "401861066",
    }


# --- the evening ----------------------------------------------------------------


async def test_matchday_and_pairing_hold_all_along(
    timeline: list[Observation],
) -> None:
    """Every refresh succeeds, shows matchday 3 and pairs all three games."""
    assert all(o.success for o in timeline)
    assert {o.matchday for o in timeline} == {MATCHDAY}
    assert {o.sensor_state for o in timeline} == {"3. Spieltag"}
    in_window = [o for o in timeline if o.espn_requests]
    assert in_window
    for observation in in_window:
        assert {name: match.espn_id for name, match in observation.matches.items()} == {
            EARLY: "401861057",
            VAR: "401861063",
            LEAD_CHANGES: "401861066",
        }
        # The entity carries what the coordinator produced.
        assert observation.sensor_scores == {
            name: match.live_score for name, match in observation.matches.items()
        }


async def test_status_sequence_without_flapping(timeline: list[Observation]) -> None:
    """pre -> live -> ht -> live -> post, IN_PROGRESS counts as live."""
    for name in GAMES:
        assert collapse(o.matches[name].status for o in timeline) == [
            "pre",
            "live",
            "ht",
            "live",
            "post",
        ], name
        # ESPN swaps FIRST_HALF/SECOND_HALF for the generic IN_PROGRESS and back
        # all through the game; none of it may leak into the status.
        in_progress = [
            o
            for o in timeline
            if espn_status(o.snapshot.event(name)) == ("in", "STATUS_IN_PROGRESS")
        ]
        assert in_progress, name
        assert {o.matches[name].status for o in in_progress} == {"live"}, name


async def test_live_data_follows_recording(timeline: list[Observation]) -> None:
    """Status, minute, score, scorers and cards match the served snapshot."""
    for observation in (o for o in timeline if o.espn_requests):
        for name in GAMES:
            event = observation.snapshot.event(name)
            match = observation.matches[name]
            status = "post" if match.oldb_score else expected_status(event)
            assert match.status == status, (observation.time, name)
            assert match.minute == competition_of(event)["status"]["displayClock"]
            assert match.live_score == (
                None if status == "pre" else espn_score(event)
            ), (observation.time, name)
            # ESPN lists details chronologically; the sort by displayed minute
            # must keep that order although stoppage-time clocks are capped.
            assert match.scorers == detail_entries(event, scoring=True)
            assert match.cards == detail_entries(event, scoring=False)

    scores = {
        name: collapse(o.matches[name].live_score for o in timeline) for name in GAMES
    }
    assert scores == {
        EARLY: [None, "0:0"],
        VAR: [None, "0:0", "0:1", "1:1", "1:2", "2:2", "1:2"],
        LEAD_CHANGES: [None, "0:0", "0:1", "1:1", "2:1", "2:2", "2:3"],
    }


async def test_minute_with_stoppage_time(timeline: list[Observation]) -> None:
    """``minute`` shows stoppage time, half time keeps 45'+x, 90'+6' at the end."""
    minutes = collapse(
        o.matches[LEAD_CHANGES].minute for o in timeline if o.espn_requests
    )
    assert minutes[:3] == ["0'", "1'", "2'"]
    assert minutes[-7:] == [
        "90'",
        "90'+1'",
        "90'+2'",
        "90'+3'",
        "90'+4'",
        "90'+5'",
        "90'+6'",
    ]
    assert "45'+1'" in minutes
    half_time = {
        o.matches[LEAD_CHANGES].minute
        for o in timeline
        if o.matches[LEAD_CHANGES].status == "ht"
    }
    assert half_time == {"45'+1'"}

    # A stoppage-time event carries the capped clock value (5400, like 90'),
    # only "90'+5'" orders it after the goals.
    final = timeline[-1].matches[VAR]
    assert final.cards == [
        ("36'", "Lukás Cerv", "team1"),
        ("66'", "Luka Vuskovic", "team2"),
        ("90'+5'", "Alex Král", "team1"),
    ]


async def test_var_goal_taken_back(timeline: list[Observation]) -> None:
    """Current behaviour: the annulled goal outlives the score by one snapshot.

    79' Radosta makes it 2:2. At 22:25:01 local ESPN is back at 1:2 but still
    lists the goal; 30 seconds later it is gone. Matchday passes both through
    unchanged (no smoothing), so for 30 seconds the sensor shows 1:2 with two
    scorers for the home side.
    """

    def at(when: datetime) -> tuple[str | None, list[str]]:
        observation = next(o for o in timeline if o.time == when)
        match = observation.matches[VAR]
        return match.live_score, [name for _minute, name, _team in match.scorers]

    before = ["Luka Modric", "Adam Karabec", "Marco Pasalic"]
    assert at(utc(20, 22, 31)) == ("1:2", before)
    assert at(utc(20, 23, 1)) == ("2:2", [*before, "Matěj Radosta"])
    assert at(utc(20, 25, 1)) == ("1:2", [*before, "Matěj Radosta"])
    assert at(utc(20, 25, 31)) == ("1:2", before)


async def test_score_and_scorers_disagree_only_briefly(
    timeline: list[Observation],
) -> None:
    """Documents every moment where scorers and score do not add up.

    ESPN adds a goal to ``details`` up to 30 s before it raises the score (three
    times) and drops the VAR-annulled goal 30 s after lowering the score (once).
    """
    mismatches: set[tuple[datetime, str, str, int]] = set()
    for observation in timeline:
        for name in GAMES:
            match = observation.matches[name]
            if match.live_score is None:
                continue
            home, away = (int(goals) for goals in match.live_score.split(":"))
            team1 = sum(team == "team1" for *_rest, team in match.scorers)
            team2 = len(match.scorers) - team1
            if (team1, team2) != (home, away):
                mismatches.add(
                    (
                        observation.snapshot.ts,
                        name,
                        match.live_score,
                        len(match.scorers),
                    )
                )

    assert sorted(mismatches) == [
        (utc(18, 51, 31), LEAD_CHANGES, "0:0", 1),  # Yamal 2', score follows
        (utc(19, 29, 31), LEAD_CHANGES, "1:1", 3),  # Kane 40', score follows
        (utc(19, 58, 1), VAR, "0:1", 2),  # Karabec 54', score follows
        (utc(20, 25, 1), VAR, "1:2", 4),  # Radosta 79' annulled, detail follows
    ]


# --- polling --------------------------------------------------------------------


async def test_polling_cadence(timeline: list[Observation]) -> None:
    """30 min before the window, 5 min in it, 60 s around live games, and back."""
    changes = [
        (o.time, o.interval)
        for previous, o in zip([None, *timeline], timeline)
        if previous is None or previous.interval != o.interval
    ]
    times = [time for time, _interval in changes]
    assert [interval for _time, interval in changes] == [
        INTERVAL_IDLE,  # Thursday night: matchday shown, ESPN not asked yet
        INTERVAL_WINDOW,  # local midnight: the day before the matchday
        INTERVAL_LIVE,  # 10 min before SVN - SCO
        INTERVAL_WINDOW,  # its final whistle
        INTERVAL_LIVE,  # before the evening games
        INTERVAL_WINDOW,  # after both final whistles
        INTERVAL_IDLE,  # the window closes two days later
    ]
    assert times[0] == START
    assert times[1] == utc(22, 0, day=24)
    assert times[2] == KICKOFF[EARLY] - KICKOFF_LEAD
    assert times[3] == RECORDING.final_whistle(EARLY)
    # In 5-minute steps the fast cadence starts 5 to 10 minutes before kickoff.
    assert (
        KICKOFF[LEAD_CHANGES] - KICKOFF_LEAD
        <= times[4]
        < KICKOFF[LEAD_CHANGES] - KICKOFF_LEAD + INTERVAL_WINDOW
    )
    assert times[5] == max(
        RECORDING.final_whistle(VAR), RECORDING.final_whistle(LEAD_CHANGES)
    )
    assert WINDOW_CLOSES <= times[6] < WINDOW_CLOSES + INTERVAL_WINDOW

    # ESPN is left alone until the window opens, then asked on every refresh.
    assert not any(o.espn_requests for o in timeline if o.time < times[1])
    assert all(o.espn_requests == 1 for o in timeline if o.time >= times[1])
    # Every live refresh used the fast cadence.
    assert all(o.interval == INTERVAL_LIVE for o in timeline if live(o))


async def test_timer_follows_interval(replay: Replay) -> None:
    """Home Assistant refreshes after exactly the interval the coordinator set."""
    await replay.async_setup(utc(19, 10))
    assert replay.coordinator.update_interval == INTERVAL_LIVE
    requests = len(replay.requests)

    replay.freezer.tick(INTERVAL_LIVE - timedelta(seconds=1))
    async_fire_time_changed(replay.hass)
    await replay.hass.async_block_till_done()
    assert len(replay.requests) == requests

    replay.freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(replay.hass)
    await replay.hass.async_block_till_done()
    assert len(replay.requests) > requests


# --- matchday lock ----------------------------------------------------------------


async def test_no_lookups_while_live(timeline: list[Observation]) -> None:
    """With a live match on display, last/next match are not even asked."""
    after_live = [o for previous, o in pairwise(timeline) if live(previous)]
    assert after_live
    assert all(o.lookups == 0 for o in after_live)
    others = [o for previous, o in pairwise(timeline) if not live(previous)]
    assert all(o.lookups == 2 for o in others)


async def test_live_matchday_is_not_switched(replay: Replay) -> None:
    """OpenLigaDB pointing at another matchday is ignored until the games end."""
    await replay.async_setup(utc(20, 20))
    assert replay.observations[-1].matchday == MATCHDAY
    replay.lookup_override = NEXT_MATCH  # last and next: matchday 4

    await replay.async_run_until(utc(20, 45), RECORDING.changes())

    # The lock follows the data shown before a refresh: the refresh that sees
    # the last final whistle is still locked, the next one asks and follows.
    last_live = max(i for i, o in enumerate(replay.observations) if live(o))
    held = replay.observations[1 : last_live + 2]
    assert len(held) > 20
    assert all(o.matchday == MATCHDAY and o.lookups == 0 for o in held)
    assert not live(held[-1])
    released = replay.observations[last_live + 2]
    assert released.lookups == 2
    assert released.matchday == NEXT_MATCH["group"]["groupOrderID"]
    assert released.sensor_state == "4. Spieltag"


# --- after the final whistle --------------------------------------------------------


async def test_after_final_whistle(timeline: list[Observation]) -> None:
    """Final scores stay, the stopped ESPN clock keeps nothing "hot"."""
    finished = [o for o in timeline if o.time >= RECORDING.final_whistle(LEAD_CHANGES)]
    assert finished
    for observation in finished:
        assert {m.status for m in observation.matches.values()} == {"post"}
        assert observation.interval in (INTERVAL_WINDOW, INTERVAL_IDLE)

    final = timeline[-1].matches
    assert {name: m.live_score for name, m in final.items()} == {
        EARLY: "0:0",
        VAR: "1:2",
        LEAD_CHANGES: "2:3",
    }
    # ESPN leaves the clock at the last stoppage minute.
    assert {name: m.minute for name, m in final.items()} == {
        EARLY: "90'+2'",
        VAR: "90'+5'",
        LEAD_CHANGES: "90'+6'",
    }
    assert final[LEAD_CHANGES].scorers == [
        ("2'", "Lamine Yamal", "team2"),
        ("36'", "Anthony Gordon", "team1"),
        ("40'", "Harry Kane", "team1"),
        ("60'", "Álex Baena", "team2"),
        ("74'", "Mikel Oyarzabal", "team2"),
    ]
    # OpenLigaDB's result arrives later and agrees with ESPN.
    for name in GAMES:
        entered = RECORDING.final_whistle(name) + OLDB_RESULT_DELAY
        assert {o.matches[name].oldb_score for o in timeline if o.time < entered} == {
            None
        }
        assert {o.matches[name].oldb_score for o in timeline if o.time >= entered} == {
            final[name].live_score
        }


# --- match summaries ------------------------------------------------------------------


def summary_fetches(timeline: list[Observation], name: str) -> list[Observation]:
    """Return the observations whose refresh requested the summary of ``name``."""
    return [o for o in timeline if o.summary_requests.get(EVENT_IDS[name])]


async def test_summaries_only_around_the_games(
    timeline: list[Observation],
) -> None:
    """Before kickoff only in the line-up window and every PRE_REFRESH, one per
    refresh at most, none after the day."""
    for observation in timeline:
        assert all(count == 1 for count in observation.summary_requests.values())
    for name in GAMES:
        before = [
            o.time
            for o in summary_fetches(timeline, name)
            if o.matches[name].status == "pre"
        ]
        # The recording has no summaries before kickoff (HTTP 404): the line-ups
        # stay unknown and the window is asked every PRE_REFRESH.
        assert before, name
        assert min(before) >= KICKOFF[name] - LINEUP_LEAD, name
        assert max(before) < KICKOFF[name] + LINEUP_GRACE, name
        for previous, current in pairwise(before):
            assert current - previous >= PRE_REFRESH - timedelta(seconds=15), name
    last_fetch = max(o.time for o in timeline if o.summary_requests)
    assert last_fetch < (
        RECORDING.final_whistle(LEAD_CHANGES) + SUMMARY_SETTLE + POST_REFRESH * 2
    )


async def test_summary_every_three_minutes_or_on_status_change(
    timeline: list[Observation],
) -> None:
    """While live, fetches are three minutes apart unless the status changed."""
    for name in GAMES:
        fetches = summary_fetches(timeline, name)
        assert fetches, name
        for previous, current in pairwise(fetches):
            status_changed = (
                previous.matches[name].status != current.matches[name].status
            )
            assert status_changed or (
                current.time - previous.time >= SUMMARY_REFRESH - timedelta(seconds=15)
            ), (name, previous.time, current.time)


async def test_summary_details_grow_during_the_game(
    timeline: list[Observation],
) -> None:
    """ESP @ ENG: lineups from kickoff, substitutions and half time grow in."""
    fetched = [
        o.matches[LEAD_CHANGES]
        for o in summary_fetches(timeline, LEAD_CHANGES)
        if o.matches[LEAD_CHANGES].status != "pre"
    ]
    assert all(match.lineups for match in fetched)
    subs = [match.substitutions for match in fetched]
    assert subs == sorted(subs)
    assert subs[-1] == 9
    after_half_time = [
        o.matches[LEAD_CHANGES].ht_score
        for o in timeline
        if o.time >= KICKOFF[LEAD_CHANGES] + timedelta(minutes=50)
    ]
    assert set(after_half_time) == {"2:1"}
    assert timeline[-1].matches[LEAD_CHANGES].stats == {
        "possession": [47, 53],
        "shots": [13, 15],
        "shots_on": [5, 5],
        "corners": [6, 3],
    }


async def test_finished_game_refetched_until_settled_then_kept(
    timeline: list[Observation],
) -> None:
    """Every POST_REFRESH until settled after the whistle; the details stay until Monday."""
    whistle = RECORDING.final_whistle(LEAD_CHANGES)
    after = [o for o in summary_fetches(timeline, LEAD_CHANGES) if o.time >= whistle]
    assert len(after) > 2
    for previous, current in pairwise(after[1:]):
        assert current.time - previous.time >= POST_REFRESH - timedelta(seconds=15)
    assert whistle + SUMMARY_SETTLE <= after[-1].time
    assert after[-1].time < whistle + SUMMARY_SETTLE + POST_REFRESH * 2
    final = timeline[-1].matches[LEAD_CHANGES]
    assert final.substitutions == 9
    assert final.lineups
    assert final.ht_score == "2:1"
    assert all(
        o.matches[LEAD_CHANGES].substitutions == 9
        for o in timeline
        if o.time >= after[-1].time
    )


async def test_missing_summaries_are_throttled(timeline: list[Observation]) -> None:
    """ESPN answers 404 for the other two games: placeholders, few retries."""
    for name in (EARLY, VAR):
        final = timeline[-1].matches[name]
        assert (final.substitutions, final.ht_score, final.stats, final.lineups) == (
            0,
            None,
            None,
            False,
        )
        whistle = RECORDING.final_whistle(name)
        after = [o for o in summary_fetches(timeline, name) if o.time >= whistle]
        assert len(after) == MAX_POST_FAILURES, name


# --- match events ---------------------------------------------------------------


def fired(timeline: list[Observation], game: str) -> list[FiredEvent]:
    """Return the match events of ``game`` over the whole replay."""
    return [event for o in timeline for event in o.events if event.game == game]


async def test_match_events_follow_the_score(timeline: list[Observation]) -> None:
    """Every goal once, in order, with its scorer; the VAR goal taken back."""

    def summary(game: str) -> list[tuple[str, str, str | None, str | None]]:
        return [
            (
                event.event_type.removeprefix("matchday_"),
                event.data["score"],
                event.data.get("scorer"),
                event.data.get("minute") if "scorer" in event.data else None,
            )
            for event in fired(timeline, game)
        ]

    assert summary(EARLY) == [
        ("kickoff", "0:0", None, None),
        ("final_whistle", "0:0", None, None),
    ]
    assert summary(VAR) == [
        ("kickoff", "0:0", None, None),
        ("goal", "0:1", "Luka Modric", "47'"),
        ("goal", "1:1", "Adam Karabec", "54'"),
        ("goal", "1:2", "Marco Pasalic", "77'"),
        ("goal", "2:2", "Matěj Radosta", "79'"),
        ("goal_disallowed", "1:2", "Matěj Radosta", "79'"),
        ("final_whistle", "1:2", None, None),
    ]
    assert summary(LEAD_CHANGES) == [
        ("kickoff", "0:0", None, None),
        ("goal", "0:1", "Lamine Yamal", "2'"),
        ("goal", "1:1", "Anthony Gordon", "36'"),
        ("goal", "2:1", "Harry Kane", "40'"),
        ("goal", "2:2", "Álex Baena", "60'"),
        ("goal", "2:3", "Mikel Oyarzabal", "74'"),
        ("final_whistle", "2:3", None, None),
    ]


async def test_match_events_fire_after_the_sensor_update(
    timeline: list[Observation],
) -> None:
    """When an event fires, the sensor already shows its score and status."""
    events = [event for o in timeline for event in o.events]
    assert len(events) == 2 + 7 + 7
    for event in events:
        assert event.sensor_score == event.data["score"], event


async def test_match_events_carry_teams_and_context(
    timeline: list[Observation],
) -> None:
    kickoff, first_goal, *_rest, final = fired(timeline, LEAD_CHANGES)
    assert kickoff.data["status"] == "live"
    assert kickoff.data["kickoff"] == "2026-09-26T18:45:00Z"
    assert first_goal.data["team1"] == "England"
    assert first_goal.data["team2"] == "Spain"
    assert first_goal.data["team"] == "team2"
    assert first_goal.data["team_name"] == "Spain"
    assert first_goal.data["goal_type"] == "regular"
    assert first_goal.data["competition"] == REPLAY_COMPETITION.key
    assert first_goal.data["matchday"] == MATCHDAY
    assert final.data["status"] == "post"
    assert final.data["ht_score"] == "2:1"
    # Kickoff within one live refresh of the recorded start, final whistle
    # in the refresh that first shows the game finished.
    observation_of = {id(event): o.time for o in timeline for event in o.events}
    started = next(
        snap.ts
        for snap in RECORDING.snapshots
        if espn_status(snap.event(LEAD_CHANGES))[0] == "in"
    )
    assert observation_of[id(kickoff)] - started <= INTERVAL_LIVE
    assert observation_of[id(final)] == next(
        o.time for o in timeline if o.matches[LEAD_CHANGES].status == "post"
    )

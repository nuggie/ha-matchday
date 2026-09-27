"""Tests for the coordinator: fetching, merging, error handling, polling."""

from __future__ import annotations

import copy
from datetime import timedelta
from http import HTTPStatus
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .common import (
    ESPN,
    OLDB,
    espn_event,
    load_bundle,
    load_json,
    oldb_match,
    standings_url,
    summary_url,
)
from custom_components.matchday.const import CONF_COMPETITION, DOMAIN
from custom_components.matchday.coordinator import MatchdayCoordinator
from custom_components.matchday.logic.matchday import (
    INTERVAL_IDLE,
    INTERVAL_LIVE,
    INTERVAL_WINDOW,
)

LAST = load_json("oldb_bl1_lastmatch.json")
NEXT = load_json("oldb_bl1_nextmatch.json")
MATCHDAY_4 = load_json("oldb_bl1_2026_4.json")
SCOREBOARD = load_json("espn_ger1_202609.json")
BL1 = load_bundle("bl1")
SUMMARIES: dict[str, Any] = BL1["summaries"]
STANDINGS = BL1["standings"]
OLDB_TABLE = BL1["oldb_table"]

ENTITY_ID = "sensor.matchday_bundesliga_matchday"
LAST_URL = f"{OLDB}/getlastmatchbyleagueshortcut/bl1"
NEXT_URL = f"{OLDB}/getnextmatchbyleagueshortcut/bl1"


def matchdata_url(group: int, season: int = 2026) -> str:
    return f"{OLDB}/getmatchdata/bl1/{season}/{group}"


def scoreboard_url(month: str) -> str:
    return f"{ESPN}/ger.1/scoreboard?dates={month}"


def mock_sources(
    aioclient_mock: AiohttpClientMocker,
    *,
    last: Any = LAST,
    next_: Any = NEXT,
    matchdays: dict[int, Any] | None = None,
    scoreboards: dict[str, Any] | None = None,
    summaries: dict[str, Any] | None = None,
    standings: Any = STANDINGS,
    oldb_table: Any = OLDB_TABLE,
) -> None:
    """Register answers; a value that is an HTTPStatus becomes an error answer.

    Summaries default to the recorded ones of matchday 4, standings and the
    OpenLigaDB table to those recorded on 2026-09-27.
    """
    aioclient_mock.clear_requests()
    answers: dict[str, Any] = {
        LAST_URL: last,
        NEXT_URL: next_,
        standings_url("ger.1"): standings,
        f"{OLDB}/getbltable/bl1/2026": oldb_table,
    }
    for group, data in (
        matchdays if matchdays is not None else {4: MATCHDAY_4}
    ).items():
        answers[matchdata_url(group)] = data
    for month, data in (
        scoreboards if scoreboards is not None else {"202609": SCOREBOARD}
    ).items():
        answers[scoreboard_url(month)] = data
    for event_id, data in (summaries if summaries is not None else SUMMARIES).items():
        answers[summary_url("ger.1", event_id)] = data
    for url, data in answers.items():
        if isinstance(data, HTTPStatus):
            aioclient_mock.get(url, status=data)
        else:
            aioclient_mock.get(url, json=data)


def called(aioclient_mock: AiohttpClientMocker, fragment: str) -> int:
    """Count requests whose URL contains ``fragment``."""
    return sum(fragment in str(call[1]) for call in aioclient_mock.mock_calls)


@pytest.fixture(autouse=True)
async def berlin_time_zone(hass: HomeAssistant) -> None:
    """Run in the time zone the reference template sensors run in."""
    await hass.config.async_set_time_zone("Europe/Berlin")


async def setup_bl1(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bundesliga",
        unique_id="bl1",
        data={CONF_COMPETITION: "bl1"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def coordinator_of(entry: MockConfigEntry) -> MatchdayCoordinator:
    return entry.runtime_data


async def test_setup_shows_recorded_matchday(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """In the break after matchday 4 it is shown with ESPN data (round_past)."""
    freezer.move_to("2026-09-26T10:00:00Z")
    mock_sources(aioclient_mock)
    entry = await setup_bl1(hass)

    assert entry.state is ConfigEntryState.LOADED
    coordinator = coordinator_of(entry)
    assert coordinator.data.matchday == 4
    assert coordinator.data.name == "4. Spieltag"
    assert coordinator.data.espn_queried
    assert called(aioclient_mock, "scoreboard?dates=202609") == 1
    assert all(match["espn_id"] for match in coordinator.data.matches)
    assert coordinator.update_interval == INTERVAL_IDLE


async def test_window_polls_every_five_minutes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Up to two days after the matchday the medium interval applies."""
    freezer.move_to("2026-09-21T10:00:00Z")
    mock_sources(aioclient_mock)
    entry = await setup_bl1(hass)
    assert coordinator_of(entry).update_interval == INTERVAL_WINDOW


async def test_next_matchday_before_window_skips_espn(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """After mid-break the next matchday shows; ESPN is not asked yet."""
    freezer.move_to("2026-10-01T10:00:00Z")
    mock_sources(aioclient_mock, matchdays={5: [NEXT]}, scoreboards={})
    entry = await setup_bl1(hass)

    coordinator = coordinator_of(entry)
    assert coordinator.data.matchday == 5
    assert coordinator.data.name == "5. Spieltag"
    assert not coordinator.data.espn_queried
    assert called(aioclient_mock, "scoreboard") == 0
    assert coordinator.data.matches[0]["status"] == "pre"


@pytest.mark.parametrize(
    "broken",
    [
        {"matchdays": {4: HTTPStatus.INTERNAL_SERVER_ERROR}},
        {"matchdays": {4: []}},
        {"matchdays": {4: {"unexpected": "object"}}},
        {"scoreboards": {"202609": HTTPStatus.BAD_REQUEST}},
        {"scoreboards": {"202609": {"no": "events"}}},
        {"scoreboards": {"202609": {"events": []}}},
    ],
    ids=[
        "oldb-500",
        "oldb-empty",
        "oldb-garbage",
        "espn-400",
        "espn-garbage",
        "espn-lost-events",
    ],
)
async def test_failure_keeps_last_good_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    broken: dict[str, Any],
) -> None:
    """A failing or empty answer fails the update and keeps the shown matchday."""
    freezer.move_to("2026-09-26T10:00:00Z")
    mock_sources(aioclient_mock)
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)
    good = coordinator.data

    mock_sources(aioclient_mock, **broken)
    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert coordinator.data is good
    state = hass.states.get(ENTITY_ID)
    assert state.state == "4. Spieltag"
    assert len(state.attributes["matches"]) == 9


async def test_lookup_failure_keeps_matchday(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without last/next match the previous matchday stays, even after mid-break."""
    freezer.move_to("2026-09-26T10:00:00Z")
    mock_sources(aioclient_mock)
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)

    freezer.move_to("2026-10-05T10:00:00Z")
    mock_sources(
        aioclient_mock,
        last=HTTPStatus.SERVICE_UNAVAILABLE,
        next_=HTTPStatus.SERVICE_UNAVAILABLE,
    )
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.matchday == 4


async def test_live_match_polls_fast_and_blocks_switch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """While a match runs: 60 s interval, no last/next lookups, no switch."""
    scoreboard = copy.deepcopy(SCOREBOARD)
    for event in scoreboard["events"]:
        if event["id"] == "401884782":  # Paderborn - Hoffenheim, 20.09. 17:30 UTC
            competition = event["competitions"][0]
            competition["status"]["type"].update(state="in", name="STATUS_SECOND_HALF")
            competition["status"]["displayClock"] = "63'"
    matchday = copy.deepcopy(MATCHDAY_4)
    for match in matchday:
        if match["matchID"] == 83191:
            match["matchIsFinished"] = False
            match["matchResults"] = []

    freezer.move_to("2026-09-20T18:40:00Z")
    mock_sources(
        aioclient_mock, matchdays={4: matchday}, scoreboards={"202609": scoreboard}
    )
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)
    live = next(m for m in coordinator.data.matches if m["matchID"] == 83191)
    assert (live["status"], live["minute"], live["live_score"]) == (
        "live",
        "63'",
        "3:1",
    )
    assert coordinator.update_interval == INTERVAL_LIVE

    # Even if OpenLigaDB suddenly pointed elsewhere, the running matchday stays.
    freezer.tick(timedelta(minutes=1))
    mock_sources(
        aioclient_mock,
        last=NEXT,
        next_=NEXT,
        matchdays={4: matchday},
        scoreboards={"202609": scoreboard},
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.matchday == 4
    assert called(aioclient_mock, "lastmatch") == 0
    assert called(aioclient_mock, "nextmatch") == 0


async def test_month_boundary_queries_both_months(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A matchday across a month boundary asks ESPN for both months."""
    matchday = [
        oldb_match(1, "2026-09-30T18:30:00Z", "Hamburger SV", "1. FC Köln", group=6),
        oldb_match(
            2, "2026-10-01T18:30:00Z", "VfB Stuttgart", "Borussia Dortmund", group=6
        ),
    ]
    hsv = espn_event("hsv", "2026-09-30T18:30Z", "Hamburg SV", "FC Cologne")
    vfb = espn_event("vfb", "2026-10-01T18:30Z", "VfB Stuttgart", "Borussia Dortmund")
    freezer.move_to("2026-09-30T12:00:00Z")
    mock_sources(
        aioclient_mock,
        last=LAST,
        next_=matchday[0],
        matchdays={6: matchday},
        # Month answers overlap at the edges; the event must count once.
        scoreboards={"202609": {"events": [hsv, vfb]}, "202610": {"events": [vfb]}},
    )
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)

    assert called(aioclient_mock, "dates=202609") == 1
    assert called(aioclient_mock, "dates=202610") == 1
    assert [m["espn_id"] for m in coordinator.data.matches] == ["hsv", "vfb"]


async def test_first_refresh_failure_retries_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without any data yet the entry is retried later."""
    freezer.move_to("2026-09-26T10:00:00Z")
    mock_sources(aioclient_mock, matchdays={4: HTTPStatus.BAD_GATEWAY})
    entry = await setup_bl1(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


# --- summaries and table ----------------------------------------------------------

PADERBORN_HOFFENHEIM = "401884782"  # matchID 83191, 20.09. 17:30 UTC
PADERBORN, HOFFENHEIM = "3307", "7911"


def live_sources(state: str = "in", name: str = "STATUS_SECOND_HALF") -> dict[str, Any]:
    """Matchday 4 with Paderborn - Hoffenheim running (or in ``state``)."""
    scoreboard = copy.deepcopy(SCOREBOARD)
    for event in scoreboard["events"]:
        if event["id"] == PADERBORN_HOFFENHEIM:
            competition = event["competitions"][0]
            competition["status"]["type"].update(state=state, name=name)
            competition["status"]["displayClock"] = "63'"
    matchday = copy.deepcopy(MATCHDAY_4)
    for match in matchday:
        if match["matchID"] == 83191:
            match["matchIsFinished"] = False
            match["matchResults"] = []
    return {"matchdays": {4: matchday}, "scoreboards": {"202609": scoreboard}}


def summary_calls(aioclient_mock: AiohttpClientMocker) -> list[str]:
    return [
        str(call[1]).rsplit("=", 1)[1]
        for call in aioclient_mock.mock_calls
        if "/summary" in str(call[1])
    ]


def table_row(coordinator: MatchdayCoordinator, team_id: str) -> dict[str, Any]:
    return next(t for t in coordinator.data.table if t["espn_team_id"] == team_id)


async def test_summaries_follow_the_live_match(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Setup fetches every started match; later only the live one, every 3 min."""
    freezer.move_to("2026-09-20T18:40:00Z")
    mock_sources(aioclient_mock, **live_sources())
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)
    assert sorted(summary_calls(aioclient_mock)) == sorted(SUMMARIES)
    live = next(m for m in coordinator.data.matches if m["matchID"] == 83191)
    assert live["ht_score"] and live["lineups"] and live["substitutions"]

    fetched = []
    for minute in range(1, 7):
        freezer.tick(timedelta(minutes=1))
        mock_sources(aioclient_mock, **live_sources())
        await coordinator.async_refresh()
        fetched.append((minute, summary_calls(aioclient_mock)))
    assert fetched == [
        (1, []),
        (2, []),
        (3, [PADERBORN_HOFFENHEIM]),
        (4, []),
        (5, []),
        (6, [PADERBORN_HOFFENHEIM]),
    ]


async def test_summary_failure_keeps_details(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failing summary neither fails the update nor clears the details."""
    freezer.move_to("2026-09-20T18:40:00Z")
    mock_sources(aioclient_mock, **live_sources())
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)
    before = next(m for m in coordinator.data.matches if m["matchID"] == 83191)

    freezer.tick(timedelta(minutes=3))
    mock_sources(
        aioclient_mock,
        **live_sources(),
        summaries={PADERBORN_HOFFENHEIM: HTTPStatus.INTERNAL_SERVER_ERROR},
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert summary_calls(aioclient_mock) == [PADERBORN_HOFFENHEIM]
    after = next(m for m in coordinator.data.matches if m["matchID"] == 83191)
    for key in ("substitutions", "ht_score", "stats", "lineups"):
        assert after[key] == before[key]


def booked_standings() -> dict[str, Any]:
    """The recorded standings with one more game for Paderborn and Hoffenheim."""
    standings = copy.deepcopy(STANDINGS)
    for entry in standings["children"][0]["standings"]["entries"]:
        if entry["team"]["id"] in (PADERBORN, HOFFENHEIM):
            for stat in entry["stats"]:
                if stat["name"] == "gamesPlayed":
                    stat["value"] += 1
    return standings


async def test_live_table_bridges_the_gap_after_the_final_whistle(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Live: +1 game; finished: still +1 until ESPN books it; then from ESPN."""
    freezer.move_to("2026-09-20T18:40:00Z")
    mock_sources(aioclient_mock, **live_sources())
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)
    base_games = 4  # the recorded standings (27.09.)
    row = table_row(coordinator, PADERBORN)
    assert (row["matches"], row["live"], row["live_score"]) == (
        base_games + 1,
        True,
        "3:1",
    )
    assert coordinator.data.table_live
    assert table_row(coordinator, HOFFENHEIM)["live_score"] == "1:3"

    # Final whistle, ESPN has not booked the game: the delta stays.
    freezer.tick(timedelta(minutes=1))
    mock_sources(aioclient_mock, **live_sources("post", "STATUS_FULL_TIME"))
    await coordinator.async_refresh()
    row = table_row(coordinator, PADERBORN)
    assert (row["matches"], row["live"]) == (base_games + 1, False)
    assert not coordinator.data.table_live

    # Standings are asked every 5 minutes while the game waits to be booked.
    freezer.tick(timedelta(minutes=5))
    mock_sources(
        aioclient_mock,
        **live_sources("post", "STATUS_FULL_TIME"),
        standings=booked_standings(),
    )
    await coordinator.async_refresh()
    assert called(aioclient_mock, "/standings") == 1
    assert table_row(coordinator, PADERBORN)["matches"] == base_games + 1
    assert table_row(coordinator, HOFFENHEIM)["matches"] == base_games + 1


async def test_standings_cadence_and_failure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """30 min outside the matchday; a failure keeps the table and the update."""
    freezer.move_to("2026-09-26T10:00:00Z")
    mock_sources(aioclient_mock)
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)
    table = coordinator.data.table
    assert len(table) == 18
    assert called(aioclient_mock, "getbltable") == 1

    freezer.tick(timedelta(minutes=29))
    mock_sources(aioclient_mock)
    await coordinator.async_refresh()
    assert called(aioclient_mock, "/standings") == 0

    freezer.tick(timedelta(minutes=1))
    mock_sources(aioclient_mock, standings=HTTPStatus.BAD_GATEWAY)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert called(aioclient_mock, "/standings") == 1
    assert coordinator.data.table == table
    # OpenLigaDB names are not asked again for six hours.
    assert called(aioclient_mock, "getbltable") == 0


async def test_table_without_standings_or_names(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No standings: no table (yet). No OpenLigaDB table: names from the matchday."""
    freezer.move_to("2026-09-26T10:00:00Z")
    mock_sources(
        aioclient_mock,
        standings={"children": []},
        oldb_table=HTTPStatus.INTERNAL_SERVER_ERROR,
    )
    entry = await setup_bl1(hass)
    coordinator = coordinator_of(entry)
    assert coordinator.last_update_success
    assert coordinator.data.table is None

    # A failed standings request is retried after five minutes, not earlier.
    freezer.tick(timedelta(minutes=4))
    mock_sources(aioclient_mock, oldb_table=HTTPStatus.INTERNAL_SERVER_ERROR)
    await coordinator.async_refresh()
    assert called(aioclient_mock, "/standings") == 0
    assert coordinator.data.table is None

    freezer.tick(timedelta(minutes=1))
    mock_sources(aioclient_mock, oldb_table=HTTPStatus.INTERNAL_SERVER_ERROR)
    await coordinator.async_refresh()
    assert called(aioclient_mock, "/standings") == 1
    table = coordinator.data.table
    assert len(table) == 18
    # Every team played on matchday 4: names and crests from its OpenLigaDB match.
    teams = {
        match[side]["teamName"] for match in MATCHDAY_4 for side in ("team1", "team2")
    }
    assert {row["teamName"] for row in table} == teams

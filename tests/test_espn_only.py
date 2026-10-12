"""Tests for the ESPN-only competitions (UEFA): rounds, round pick, coordinator."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .common import ESPN, load_json, standings_url, summary_url
from custom_components.matchday.const import CONF_COMPETITION, DOMAIN
from custom_components.matchday.coordinator import MatchdayCoordinator
from custom_components.matchday.logic.espn_only import (
    espn_match,
    pick_round,
    round_matches,
    season_rounds,
)
from custom_components.matchday.logic.matchday import INTERVAL_LIVE

# Recorded 2026-10-12 (scripts/record_uefa.py ucl --season 2026): all scoreboard
# months of uefa.champions (07/2026-06/2027) and uefa.champions_qual (06-08/2026),
# the standings after matchday 1 and the summary of one matchday 1 match.
UCL = load_json("competitions/ucl-20261012.json.gz")
EVENTS = [
    (key.split("/")[0], event)
    for key, answer in UCL["scoreboards"].items()
    for event in answer.get("events") or []
]
ROUNDS = season_rounds(EVENTS)
BERLIN = ZoneInfo("Europe/Berlin")
MATCHDAY_ENTITY = "sensor.matchday_uefa_champions_league_matchday"
TABLE_ENTITY = "sensor.matchday_uefa_champions_league_table"

# The keys of the UEFA Jinja sensors' matches (summary_rev is Jinja-internal).
JINJA_KEYS = {
    "matchID",
    "status",
    "espn_id",
    "espn_slug",
    "espn_home_team_id",
    "espn_away_team_id",
    "matchDateTimeUTC",
    "matchIsFinished",
    "matchResults",
    "minute",
    "live_score",
    "scorers",
    "cards",
    "team1",
    "team2",
    "substitutions",
    "ht_score",
    "stats",
    "lineups",
}


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


# --- rounds -----------------------------------------------------------------------


def test_season_is_cut_into_rounds() -> None:
    """Qualifying legs a week apart, league phase matchdays Tuesday to Thursday."""
    names = [r.name for r in ROUNDS]
    assert names[:8] == [
        "1. Qualifikationsrunde · Hinspiel",
        "1. Qualifikationsrunde · Rückspiel",
        "2. Qualifikationsrunde · Hinspiel",
        "2. Qualifikationsrunde · Rückspiel",
        "3. Qualifikationsrunde · Hinspiel",
        "3. Qualifikationsrunde · Rückspiel",
        "Qualifikation Play-offs · Hinspiel",
        "Qualifikation Play-offs · Rückspiel",
    ]
    assert names[8:] == [f"{n}. Spieltag" for n in range(1, 9)]
    assert [r.number for r in ROUNDS] == list(range(1, 17))
    league = ROUNDS[8:]
    assert all(len(r.events) == 18 for r in league)
    assert all(r.slugs == ["uefa.champions"] for r in league)
    assert all(r.slugs == ["uefa.champions_qual"] for r in ROUNDS[:8])
    # Matchday 1 ran Tuesday to Thursday, matchday 8 is a single Wednesday.
    assert (ROUNDS[8].first_kickoff, ROUNDS[8].last_kickoff) == (
        utc("2026-09-08T16:45"),
        utc("2026-09-10T19:00"),
    )
    assert ROUNDS[15].first_kickoff == ROUNDS[15].last_kickoff


def test_duplicate_events_count_once() -> None:
    """Month queries overlap; an event seen twice is one match."""
    assert season_rounds(EVENTS + EVENTS) == ROUNDS


def test_stage_change_starts_a_round() -> None:
    """A different stage within the gap is a round of its own."""
    first, second = copy.deepcopy(EVENTS[0][1]), copy.deepcopy(EVENTS[0][1])
    second["id"] = "2"
    second["season"] = {"slug": "round-of-16"}
    later = utc(first["competitions"][0]["date"].rstrip("Z")) + timedelta(days=1)
    second["competitions"][0]["date"] = later.strftime("%Y-%m-%dT%H:%MZ")
    rounds = season_rounds([("a", first), ("a", second)])
    assert [r.stage for r in rounds] == [first["season"]["slug"], "round-of-16"]
    assert rounds[1].name == "Achtelfinale"


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # The day before matchday 2: closer than matchday 1 (Sep 10).
        ("2026-10-12T13:00", "2. Spieltag"),
        ("2026-10-14T22:00", "2. Spieltag"),
        # Mid-break between matchday 2 (Wed 19:00) and 3 (Tue 16:45).
        ("2026-10-17T17:00", "2. Spieltag"),
        ("2026-10-17T18:00", "3. Spieltag"),
        # Before the season and after the last round.
        ("2026-06-01T00:00", "1. Qualifikationsrunde · Hinspiel"),
        ("2027-03-01T00:00", "8. Spieltag"),
    ],
)
def test_pick_round_closer_in_time(now: str, expected: str) -> None:
    picked = pick_round(ROUNDS, utc(now))
    assert picked is not None
    assert picked.name == expected


def test_pick_round_without_rounds() -> None:
    assert pick_round([], utc("2026-10-12T13:00")) is None


# --- matches ----------------------------------------------------------------------


def test_match_format_follows_the_jinja_sensor() -> None:
    """All Jinja keys, ESPN names and logos, matchID as int, local kickoff."""
    matchday_2 = ROUNDS[9]
    matches = round_matches(matchday_2, EVENTS, BERLIN)
    assert len(matches) == 18
    first = matches[0]
    assert JINJA_KEYS <= first.keys()
    assert first["matchID"] == int(first["espn_id"])
    assert first["espn_slug"] == "uefa.champions"
    assert first["matchDateTimeUTC"] == "2026-10-13T16:45:00Z"
    assert first["matchDateTime"] == "2026-10-13T18:45:00"
    assert first["status"] == "pre"
    assert first["live_score"] is None
    assert first["matchResults"] == []
    assert first["team1"]["teamName"]
    assert first["team1"]["teamIconUrl"].startswith("https://")
    assert [m["matchDateTimeUTC"] for m in matches] == sorted(
        m["matchDateTimeUTC"] for m in matches
    )


def test_finished_match_has_score_and_scorers() -> None:
    matches = round_matches(ROUNDS[8], EVENTS, BERLIN)
    assert all(m["status"] == "post" and m["matchIsFinished"] for m in matches)
    assert all(m["live_score"] for m in matches)
    assert any(m["scorers"] for m in matches)


def _with_status(state: str, name: str, completed: bool) -> dict[str, Any]:
    event = copy.deepcopy(EVENTS[0][1])
    event["competitions"][0]["status"]["type"].update(
        state=state, name=name, completed=completed
    )
    return event


@pytest.mark.parametrize(
    ("state", "name", "completed", "status"),
    [
        ("pre", "STATUS_SCHEDULED", False, "pre"),
        ("in", "STATUS_FIRST_HALF", False, "live"),
        ("in", "STATUS_HALFTIME", False, "ht"),
        ("post", "STATUS_FULL_TIME", True, "post"),
        # Postponed: ESPN says "post" but not completed; the Jinja shows "pre".
        ("post", "STATUS_POSTPONED", False, "pre"),
    ],
)
def test_status_like_the_jinja(
    state: str, name: str, completed: bool, status: str
) -> None:
    match = espn_match(_with_status(state, name, completed), "uefa.champions", BERLIN)
    assert match is not None
    assert match["status"] == status
    assert match["matchIsFinished"] is completed


# --- coordinator ------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def berlin_time_zone(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")


def mock_ucl(
    aioclient_mock: AiohttpClientMocker,
    scoreboards: dict[str, Any] | None = None,
) -> None:
    """Season scoreboards, standings and summaries (the recorded one, 404 else)."""
    aioclient_mock.clear_requests()
    for key, answer in (scoreboards or UCL["scoreboards"]).items():
        slug, month = key.split("/")
        aioclient_mock.get(f"{ESPN}/{slug}/scoreboard?dates={month}", json=answer)
    aioclient_mock.get(standings_url("uefa.champions"), json=UCL["standings"])
    for slug, event in EVENTS:
        if (event_id := str(event["id"])) in UCL["summaries"]:
            aioclient_mock.get(
                summary_url(slug, event_id), json=UCL["summaries"][event_id]
            )
        else:
            aioclient_mock.get(summary_url(slug, event_id), status=HTTPStatus.NOT_FOUND)


async def setup_ucl(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UEFA Champions League",
        unique_id="ucl",
        data={CONF_COMPETITION: "ucl"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def called(aioclient_mock: AiohttpClientMocker, fragment: str) -> int:
    return sum(fragment in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_setup_shows_the_next_round_with_table(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Day before matchday 2: its 18 matches, the season list, 36 table rows."""
    freezer.move_to("2026-10-12T13:00:00Z")
    mock_ucl(aioclient_mock)
    entry = await setup_ucl(hass)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get(MATCHDAY_ENTITY)
    assert state is not None
    assert state.state == "2. Spieltag"
    assert state.attributes["matchday"] == 10
    assert state.attributes["season"] == 2026
    assert state.attributes["competition"] == "ucl"
    assert len(state.attributes["matches"]) == 18
    assert [d["name"] for d in state.attributes["matchdays"]][9] == "2. Spieltag"
    table = hass.states.get(TABLE_ENTITY)
    assert table is not None
    assert len(table.attributes["table"]) == 36
    # Season: 12 main + 3 qualifying months; the round: October once more.
    assert called(aioclient_mock, "uefa.champions/scoreboard?dates=202610") == 2
    assert called(aioclient_mock, "uefa.champions_qual/scoreboard") == 3
    # Nothing has kicked off: no summaries.
    assert called(aioclient_mock, "/summary") == 0


async def test_finished_round_summaries_use_the_event_slug(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """After matchday 1 every match is fetched once; the recorded one has stats."""
    freezer.move_to("2026-09-11T12:00:00Z")
    mock_ucl(aioclient_mock)
    entry = await setup_ucl(hass)
    coordinator: MatchdayCoordinator = entry.runtime_data
    assert coordinator.data.name == "1. Spieltag"
    assert called(aioclient_mock, "uefa.champions/summary?event=") == 18
    (recorded,) = UCL["summaries"]
    match = next(m for m in coordinator.data.matches if m["espn_id"] == recorded)
    assert match["stats"]
    assert match["lineups"]

    # Settled: the next refresh asks only the 17 missing summaries once more
    # (up to MAX_POST_FAILURES), not the recorded one, nor the season.
    aioclient_mock.mock_calls.clear()
    freezer.tick(timedelta(minutes=30))
    await coordinator.async_refresh()
    assert called(aioclient_mock, f"summary?event={recorded}") == 0
    assert called(aioclient_mock, "/summary") == 17
    assert called(aioclient_mock, "scoreboard?dates=202609") == 1
    assert called(aioclient_mock, "scoreboard") == 1


async def test_live_round_is_kept_and_polled_fast(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A running match keeps its round even past the mid-break switch."""
    scoreboards = copy.deepcopy(UCL["scoreboards"])
    october = scoreboards["uefa.champions/202610"]["events"]
    last = max(
        (e for e in october if e["date"].startswith("2026-10-14")),
        key=lambda e: e["date"],
    )
    last["competitions"][0]["status"]["type"].update(
        state="in", name="STATUS_SECOND_HALF", completed=False
    )
    freezer.move_to("2026-10-14T20:30:00Z")
    mock_ucl(aioclient_mock, scoreboards)
    entry = await setup_ucl(hass)
    coordinator: MatchdayCoordinator = entry.runtime_data
    assert coordinator.data.name == "2. Spieltag"
    assert coordinator.update_interval == INTERVAL_LIVE

    # Days later the match would still run (ESPN stuck): the round stays.
    freezer.move_to("2026-10-18T12:00:00Z")
    await coordinator.async_refresh()
    assert coordinator.data.name == "2. Spieltag"


async def test_season_failure_without_rounds_retries_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-12T13:00:00Z")
    mock_ucl(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{ESPN}/uefa.champions/scoreboard?dates=202607",
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
    )
    entry = await setup_ucl(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY

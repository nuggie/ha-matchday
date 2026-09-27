"""Tests for matchday browsing: ``matchdays`` attribute and the two actions.

Runs on 2026-09-26 10:00 UTC (break after matchday 4, which the sensor shows)
with the answers recorded on 2026-09-26/27 (``tests/fixtures/history``).
"""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
from http import HTTPStatus
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .common import OLDB, home_team_id, load_history, summary_url
from .test_competitions import setup_competition
from .test_coordinator import (
    MATCHDAY_4,
    SUMMARIES,
    called,
    matchdata_url,
    mock_sources,
    setup_bl1,
)
from custom_components.matchday import archive
from custom_components.matchday.const import DOMAIN
from custom_components.matchday.logic.summary import parse_summary
from custom_components.matchday.sensor import MatchdaySensor

HISTORY = load_history()
ENTITY_ID = "sensor.matchday_bundesliga_matchday"
TABLE_ID = "sensor.matchday_bundesliga_table"
NOW = "2026-09-26T10:00:00Z"

# Season opener, 1. Spieltag: FC Bayern München - VfB Stuttgart 5:1.
OPENER_ID = 83156
OPENER_EVENT = "401884817"
OPENER_SUMMARY = HISTORY["summaries"][f"ger.1/{OPENER_EVENT}"]
OPENER = next(m for m in HISTORY["matchdata"]["bl1/1"] if m["matchID"] == OPENER_ID)

DETAIL_KEYS = {"substitutions", "stats", "lineups", "ht_score", "pso"}


def history_sources(
    aioclient_mock: AiohttpClientMocker,
    *,
    matchdays: dict[int, Any] | None = None,
    scoreboards: dict[str, Any] | None = None,
    summaries: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Serve matchday 4 (current) plus the recorded matchdays 1, 2, 3, 5."""
    recorded = {
        1: HISTORY["matchdata"]["bl1/1"],
        2: HISTORY["matchdata"]["bl1/2"],
        3: HISTORY["matchdata"]["bl1/3"],
        4: MATCHDAY_4,
        5: HISTORY["matchdata"]["bl1/5"],
        99: [],
    }
    mock_sources(
        aioclient_mock,
        matchdays={**recorded, **(matchdays or {})},
        scoreboards={
            "202608": HISTORY["scoreboards"]["ger.1/202608"],
            "202609": HISTORY["scoreboards"]["ger.1/202609"],
            **(scoreboards or {}),
        },
        summaries={**SUMMARIES, OPENER_EVENT: OPENER_SUMMARY, **(summaries or {})},
        extra={f"{OLDB}/getmatchdata/{OPENER_ID}": OPENER, **(extra or {})},
    )


@pytest.fixture(autouse=True)
def recorded_break(
    aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW)
    history_sources(aioclient_mock)


async def get_matchday(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN,
        "get_matchday",
        {"entity_id": ENTITY_ID, **data},
        blocking=True,
        return_response=True,
    )


async def get_details(
    hass: HomeAssistant, match_id: int, entity_id: str = ENTITY_ID
) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN,
        "get_match_details",
        {"entity_id": entity_id, "match_id": match_id},
        blocking=True,
        return_response=True,
    )


def stored(hass_storage: dict[str, Any], entry: MockConfigEntry) -> dict[str, Any]:
    return hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]


def http_calls(aioclient_mock: AiohttpClientMocker) -> int:
    return len(aioclient_mock.mock_calls)


def requested(aioclient_mock: AiohttpClientMocker, url: str) -> int:
    """Count requests of exactly ``url``."""
    return sum(str(call[1]) == url for call in aioclient_mock.mock_calls)


SEASON_URL = f"{OLDB}/getmatchdata/bl1/2026"


# --- matchdays attribute -----------------------------------------------------


async def test_matchdays_attribute(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The sensor lists all 34 matchdays with their kickoff range."""
    await setup_bl1(hass)
    matchdays = hass.states.get(ENTITY_ID).attributes["matchdays"]
    assert len(matchdays) == 34
    assert matchdays[0] == {
        "matchday": 1,
        "name": "1. Spieltag",
        "first_kickoff": "2026-08-28T18:30:00Z",
        "last_kickoff": "2026-08-30T15:30:00Z",
    }
    assert matchdays[3]["last_kickoff"] == "2026-09-20T17:30:00Z"
    assert called(aioclient_mock, "getavailablegroups/bl1/2026") == 1
    assert requested(aioclient_mock, SEASON_URL) == 1
    assert "matchdays" in MatchdaySensor._unrecorded_attributes


async def test_matchdays_refresh_every_six_hours(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Groups and season fixtures are fetched again only after six hours."""
    entry = await setup_bl1(hass)
    coordinator = entry.runtime_data

    freezer.tick(timedelta(hours=5, minutes=59))
    history_sources(aioclient_mock)
    await coordinator.async_refresh()
    assert called(aioclient_mock, "getavailablegroups") == 0
    assert requested(aioclient_mock, SEASON_URL) == 0

    freezer.tick(timedelta(minutes=2))
    await coordinator.async_refresh()
    assert called(aioclient_mock, "getavailablegroups") == 1
    assert requested(aioclient_mock, SEASON_URL) == 1


async def test_matchdays_failure_keeps_list(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failing refresh keeps the list and the update; retry after 30 minutes."""
    entry = await setup_bl1(hass)
    coordinator = entry.runtime_data
    groups_url = f"{OLDB}/getavailablegroups/bl1/2026"

    freezer.tick(timedelta(hours=7))
    history_sources(
        aioclient_mock, extra={groups_url: HTTPStatus.INTERNAL_SERVER_ERROR}
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert len(hass.states.get(ENTITY_ID).attributes["matchdays"]) == 34

    freezer.tick(timedelta(minutes=10))
    history_sources(aioclient_mock)
    await coordinator.async_refresh()
    assert called(aioclient_mock, "getavailablegroups") == 0

    freezer.tick(timedelta(minutes=21))
    await coordinator.async_refresh()
    assert called(aioclient_mock, "getavailablegroups") == 1


async def test_matchdays_survive_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """The stored schedule is used after a reload, without new requests."""
    entry = await setup_bl1(hass)
    history_sources(aioclient_mock)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert stored(hass_storage, entry)["schedule"]["season"] == 2026
    assert called(aioclient_mock, "getavailablegroups") == 0
    assert len(hass.states.get(ENTITY_ID).attributes["matchdays"]) == 34


async def test_cup_and_premier_league_matchdays(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """DFB-Pokal lists all six rounds (later ones without kickoffs), PL 38."""
    await setup_competition(hass, aioclient_mock, freezer, "dfb")
    await setup_competition(hass, aioclient_mock, freezer, "pl")
    registry = er.async_get(hass)
    cup = hass.states.get(
        registry.async_get_entity_id("sensor", DOMAIN, "dfb_matchday")
    ).attributes["matchdays"]
    assert [r["name"] for r in cup] == [
        "1. Runde",
        "2. Runde",
        "Achtelfinale",
        "Viertelfinale",
        "Halbfinale",
        "Endspiel",
    ]
    assert cup[1]["first_kickoff"] == "2026-10-27T17:00:00Z"
    assert cup[2]["first_kickoff"] is None
    league = hass.states.get(
        registry.async_get_entity_id("sensor", DOMAIN, "pl_matchday")
    ).attributes["matchdays"]
    assert len(league) == 38
    assert league[4]["name"] == "5. Spieltag"


# --- get_matchday ------------------------------------------------------------


async def test_current_matchday_from_coordinator(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without arguments (or with the shown one) the sensor's matches come back."""
    await setup_bl1(hass)
    history_sources(aioclient_mock)
    response = await get_matchday(hass)
    assert {k: v for k, v in response.items() if k != "matches"} == {
        "competition": "bl1",
        "season": 2026,
        "matchday": 4,
        "name": "4. Spieltag",
        "is_current": True,
    }
    assert response["matches"] == hass.states.get(ENTITY_ID).attributes["matches"]
    assert response["matches"][0]["ht_score"] == "3:0"
    assert await get_matchday(hass, matchday=4, season=2026) == response
    assert await get_matchday(hass, season=2026) == response
    assert http_calls(aioclient_mock) == 0


async def test_past_matchday(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Matchday 1: OpenLigaDB + ESPN merged, summary fields empty."""
    await setup_bl1(hass)
    history_sources(aioclient_mock)
    response = await get_matchday(hass, matchday=1)

    assert response["is_current"] is False
    assert (response["matchday"], response["name"]) == (1, "1. Spieltag")
    matches = response["matches"]
    assert len(matches) == 9
    assert all(m["status"] == "post" and m["espn_id"] for m in matches)
    assert all(m["live_score"] == m["oldb_score"] for m in matches)
    assert all(
        (m["substitutions"], m["stats"], m["lineups"], m["ht_score"], m["pso"])
        == ([], None, None, None, None)
        for m in matches
    )
    opener = next(m for m in matches if m["matchID"] == OPENER_ID)
    assert (opener["espn_id"], opener["live_score"]) == (OPENER_EVENT, "5:1")
    assert opener["scorers"][0]["team"] in ("team1", "team2")
    # Same fields as the sensor's matches.
    current = hass.states.get(ENTITY_ID).attributes["matches"]
    assert set(matches[0]) == set(current[0])

    assert called(aioclient_mock, "getmatchdata/bl1/2026/1") == 1
    assert called(aioclient_mock, "dates=202608") == 1
    assert called(aioclient_mock, "/summary") == 0
    assert http_calls(aioclient_mock) == 2


async def test_settled_matchdays_are_cached(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """Matchdays 2 and 3 share a scoreboard; asking again costs nothing."""
    entry = await setup_bl1(hass)
    history_sources(aioclient_mock)
    second = await get_matchday(hass, matchday=2)
    third = await get_matchday(hass, matchday=3)
    assert called(aioclient_mock, "dates=202609") == 1
    assert http_calls(aioclient_mock) == 3

    history_sources(aioclient_mock)
    assert await get_matchday(hass, matchday=2) == second
    assert await get_matchday(hass, matchday=3, season=2026) == third
    assert http_calls(aioclient_mock) == 0

    await hass.config_entries.async_unload(entry.entry_id)
    assert set(stored(hass_storage, entry)["matchdays"]) == {"2026/2", "2026/3"}


async def test_store_survives_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """Settled matchdays and match details are stored per config entry."""
    entry = await setup_bl1(hass)
    history_sources(aioclient_mock)
    first = await get_matchday(hass, matchday=1)
    details = await get_details(hass, OPENER_ID)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    data = stored(hass_storage, entry)
    assert set(data["matchdays"]) == {"2026/1"}
    assert set(data["details"]) == {str(OPENER_ID)}

    history_sources(aioclient_mock)
    assert await get_matchday(hass, matchday=1) == first
    assert await get_details(hass, OPENER_ID) == details
    assert http_calls(aioclient_mock) == 0


async def test_store_is_bounded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Beyond the limit the oldest matchdays are dropped from the store."""
    monkeypatch.setattr(archive, "MAX_STORED_MATCHDAYS", 2)
    entry = await setup_bl1(hass)
    for matchday in (3, 1, 2):
        await get_matchday(hass, matchday=matchday)
    await hass.config_entries.async_unload(entry.entry_id)
    assert set(stored(hass_storage, entry)["matchdays"]) == {"2026/2", "2026/3"}


async def test_future_matchday_without_espn(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Matchday 5 (09.10.): OpenLigaDB only, all pre, cached for 15 minutes."""
    entry = await setup_bl1(hass)
    history_sources(aioclient_mock)
    response = await get_matchday(hass, matchday=5)
    matches = response["matches"]
    assert len(matches) == 9
    assert all(m["status"] == "pre" and m["espn_id"] is None for m in matches)
    assert all(m["live_score"] is None for m in matches)
    assert called(aioclient_mock, "scoreboard") == 0
    assert http_calls(aioclient_mock) == 1

    freezer.tick(timedelta(minutes=14))
    assert await get_matchday(hass, matchday=5) == response
    assert http_calls(aioclient_mock) == 1

    freezer.tick(timedelta(minutes=2))
    await get_matchday(hass, matchday=5)
    assert called(aioclient_mock, "getmatchdata/bl1/2026/5") == 2

    await hass.config_entries.async_unload(entry.entry_id)
    assert "2026/5" not in stored(hass_storage, entry)["matchdays"]


def _running_matchday_3() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Matchday 3 with Dortmund - Paderborn (83174, 401884799) still running."""
    matchday = copy.deepcopy(HISTORY["matchdata"]["bl1/3"])
    for match in matchday:
        if match["matchID"] == 83174:
            match["matchIsFinished"] = False
            match["matchResults"] = []
    scoreboard = copy.deepcopy(HISTORY["scoreboards"]["ger.1/202609"])
    for event in scoreboard["events"]:
        if event["id"] == "401884799":
            status = event["competitions"][0]["status"]
            status["type"].update(state="in", name="STATUS_SECOND_HALF")
            status["displayClock"] = "63'"
    return matchday, scoreboard


async def test_unsettled_matchday_and_live_details_cached_briefly(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """A running match: matchday for 15 minutes, its details for one minute."""
    entry = await setup_bl1(hass)
    matchday, scoreboard = _running_matchday_3()
    sources = {
        "matchdays": {3: matchday},
        "scoreboards": {"202609": scoreboard},
        "summaries": {"401884799": OPENER_SUMMARY},
    }
    history_sources(aioclient_mock, **sources)
    response = await get_matchday(hass, matchday=3)
    live = next(m for m in response["matches"] if m["matchID"] == 83174)
    assert (live["status"], live["minute"]) == ("live", "63'")

    details = await get_details(hass, 83174)
    assert details["lineups"] is not None
    assert called(aioclient_mock, "summary?event=401884799") == 1
    freezer.tick(timedelta(seconds=50))
    await get_details(hass, 83174)
    assert called(aioclient_mock, "summary?event=401884799") == 1
    freezer.tick(timedelta(seconds=20))
    await get_details(hass, 83174)
    assert called(aioclient_mock, "summary?event=401884799") == 2

    await get_matchday(hass, matchday=3)
    assert called(aioclient_mock, "getmatchdata/bl1/2026/3") == 1
    freezer.tick(timedelta(minutes=15))
    await get_matchday(hass, matchday=3)
    assert called(aioclient_mock, "getmatchdata/bl1/2026/3") == 2

    await hass.config_entries.async_unload(entry.entry_id)
    data = stored(hass_storage, entry)
    assert "2026/3" not in data["matchdays"]
    assert "83174" not in data["details"]


async def test_parallel_requests_fetch_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Three concurrent calls for matchday 1, two for the opener: one fetch each."""
    await setup_bl1(hass)
    gate = asyncio.Event()

    def gated(answer: Any) -> Any:
        async def respond(
            method: str, url: Any, data: Any
        ) -> AiohttpClientMockResponse:
            await gate.wait()
            return AiohttpClientMockResponse(method, url, json=answer)

        return respond

    history_sources(aioclient_mock, matchdays={1: HTTPStatus.NOT_IMPLEMENTED})
    # First registration wins: put the gated answers in front.
    aioclient_mock._mocks.insert(
        0,
        AiohttpClientMockResponse(
            "get",
            URL(matchdata_url(1)),
            side_effect=gated(HISTORY["matchdata"]["bl1/1"]),
        ),
    )
    aioclient_mock._mocks.insert(
        0,
        AiohttpClientMockResponse(
            "get",
            URL(summary_url("ger.1", OPENER_EVENT)),
            side_effect=gated(OPENER_SUMMARY),
        ),
    )

    calls = [hass.async_create_task(get_matchday(hass, matchday=1)) for _ in range(3)]
    calls += [hass.async_create_task(get_details(hass, OPENER_ID)) for _ in range(2)]
    for _ in range(20):
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*calls)

    assert results[0] == results[1] == results[2]
    assert results[3] == results[4]
    assert results[3]["ht_score"] is not None
    assert called(aioclient_mock, "getmatchdata/bl1/2026/1") == 1
    assert called(aioclient_mock, "dates=202608") == 1
    assert called(aioclient_mock, f"summary?event={OPENER_EVENT}") == 1


# --- get_match_details -------------------------------------------------------


async def test_details_of_current_match(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A match of the shown matchday: the sensor's details, no request."""
    await setup_bl1(hass)
    history_sources(aioclient_mock)
    details = await get_details(hass, 83183)
    bayern = next(
        m
        for m in hass.states.get(ENTITY_ID).attributes["matches"]
        if m["matchID"] == 83183
    )
    assert set(details) == DETAIL_KEYS | {"match_id"}
    assert details["match_id"] == 83183
    assert details == {"match_id": 83183, **{k: bayern[k] for k in DETAIL_KEYS}}
    assert details["ht_score"] == "3:0"
    assert len(details["substitutions"]) == 10
    assert http_calls(aioclient_mock) == 0


async def test_details_of_old_match(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unknown old match is looked up, its matchday loaded, summary parsed."""
    await setup_bl1(hass)
    history_sources(aioclient_mock)
    details = await get_details(hass, OPENER_ID)

    expected = parse_summary(OPENER_SUMMARY, home_team_id(OPENER_SUMMARY))
    assert details == {"match_id": OPENER_ID, **expected}
    assert details["lineups"]["home"]["players"]
    assert details["stats"]["possession"]
    assert called(aioclient_mock, f"getmatchdata/{OPENER_ID}") == 1
    assert called(aioclient_mock, "getmatchdata/bl1/2026/1") == 1
    assert called(aioclient_mock, "summary") == 1

    history_sources(aioclient_mock)
    assert await get_details(hass, OPENER_ID) == details
    # The matchday came along and is cached too.
    await get_matchday(hass, matchday=1)
    assert http_calls(aioclient_mock) == 0


async def test_details_after_browsing_skip_lookup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A match of a browsed matchday needs only its summary."""
    await setup_bl1(hass)
    await get_matchday(hass, matchday=1)
    history_sources(aioclient_mock)
    await get_details(hass, OPENER_ID)
    assert http_calls(aioclient_mock) == 1
    assert called(aioclient_mock, "summary") == 1


async def test_future_match_has_no_details(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A match that has not started: empty details, no ESPN request."""
    await setup_bl1(hass)
    await get_matchday(hass, matchday=5)
    history_sources(aioclient_mock)
    details = await get_details(hass, 83192)
    assert details == {
        "match_id": 83192,
        "substitutions": [],
        "stats": None,
        "lineups": None,
        "ht_score": None,
        "pso": None,
    }
    assert http_calls(aioclient_mock) == 0


# --- errors ------------------------------------------------------------------


@pytest.mark.parametrize("entity_id", [TABLE_ID, "sensor.does_not_exist"])
async def test_invalid_entity(hass: HomeAssistant, entity_id: str) -> None:
    await setup_bl1(hass)
    with pytest.raises(ServiceValidationError) as err:
        await get_matchday(hass, entity_id=entity_id)
    assert err.value.translation_key == "invalid_entity"
    with pytest.raises(ServiceValidationError) as err:
        await get_details(hass, OPENER_ID, entity_id=entity_id)
    assert err.value.translation_key == "invalid_entity"


async def test_entry_not_loaded(hass: HomeAssistant) -> None:
    entry = await setup_bl1(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    with pytest.raises(ServiceValidationError) as err:
        await get_matchday(hass, matchday=1)
    assert err.value.translation_key == "entry_not_loaded"


async def test_unknown_matchday(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Matchday 99 has no fixtures and is not in the season's list."""
    await setup_bl1(hass)
    history_sources(aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await get_matchday(hass, matchday=99)
    assert err.value.translation_key == "unknown_matchday"
    assert err.value.translation_placeholders == {
        "competition": "Bundesliga",
        "matchday": "99",
        "season": "2026",
    }
    assert str(err.value) == "Bundesliga has no matchday 99 in season 2026"
    assert called(aioclient_mock, "getavailablegroups") == 0


async def test_other_season_needs_matchday(hass: HomeAssistant) -> None:
    await setup_bl1(hass)
    with pytest.raises(ServiceValidationError) as err:
        await get_matchday(hass, season=2025)
    assert err.value.translation_key == "matchday_required"


async def test_unknown_matchday_of_other_season(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Other seasons ask OpenLigaDB for their matchday list."""
    await setup_bl1(hass)
    history_sources(
        aioclient_mock,
        extra={
            matchdata_url(40, 2025): [],
            f"{OLDB}/getavailablegroups/bl1/2025": [],
        },
    )
    with pytest.raises(ServiceValidationError) as err:
        await get_matchday(hass, matchday=40, season=2025)
    assert err.value.translation_key == "unknown_matchday"
    assert called(aioclient_mock, "getavailablegroups/bl1/2025") == 1


@pytest.mark.parametrize(
    ("broken", "fragment"),
    [
        ({"matchdays": {1: HTTPStatus.INTERNAL_SERVER_ERROR}}, "getmatchdata"),
        ({"scoreboards": {"202608": HTTPStatus.BAD_REQUEST}}, "scoreboard"),
        ({"scoreboards": {"202608": {"no": "events"}}}, "scoreboard"),
    ],
    ids=["oldb-500", "espn-400", "espn-garbage"],
)
async def test_api_errors_leave_coordinator_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    broken: dict[str, Any],
    fragment: str,
) -> None:
    """A failing source fails the action, not the sensor."""
    entry = await setup_bl1(hass)
    coordinator = entry.runtime_data
    good = coordinator.data
    history_sources(aioclient_mock, **broken)
    with pytest.raises(HomeAssistantError) as err:
        await get_matchday(hass, matchday=1)
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "api_error"
    assert fragment in err.value.translation_placeholders["error"]
    assert coordinator.data is good
    assert coordinator.last_update_success
    assert hass.states.get(ENTITY_ID).state == "4. Spieltag"

    # Nothing broken was cached: the next call succeeds.
    history_sources(aioclient_mock)
    assert len((await get_matchday(hass, matchday=1))["matches"]) == 9


async def test_summary_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    await setup_bl1(hass)
    history_sources(
        aioclient_mock, summaries={OPENER_EVENT: HTTPStatus.SERVICE_UNAVAILABLE}
    )
    with pytest.raises(HomeAssistantError) as err:
        await get_details(hass, OPENER_ID)
    assert err.value.translation_key == "api_error"


@pytest.mark.parametrize(
    "answer",
    [HTTPStatus.NOT_FOUND, {**OPENER, "leagueShortcut": "bl2"}],
    ids=["unknown", "other-league"],
)
async def test_unknown_match(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, answer: Any
) -> None:
    await setup_bl1(hass)
    history_sources(aioclient_mock, extra={f"{OLDB}/getmatchdata/{OPENER_ID}": answer})
    with pytest.raises(ServiceValidationError) as err:
        await get_details(hass, OPENER_ID)
    assert err.value.translation_key == "unknown_match"
    assert err.value.translation_placeholders["match_id"] == str(OPENER_ID)


# --- cups, cleanup -----------------------------------------------------------


async def test_cup_future_and_empty_rounds(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Round 2 (27.10.) without ESPN; the round of 16 has no fixtures yet."""
    await setup_competition(hass, aioclient_mock, freezer, "dfb")
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "dfb_matchday")
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{OLDB}/getmatchdata/dfb/2026/2", json=HISTORY["matchdata"]["dfb/2"]
    )
    aioclient_mock.get(f"{OLDB}/getmatchdata/dfb/2026/3", json=[])

    second = await get_matchday(hass, entity_id=entity_id, matchday=2)
    assert (second["name"], len(second["matches"])) == ("2. Runde", 16)
    assert all(m["status"] == "pre" for m in second["matches"])
    third = await get_matchday(hass, entity_id=entity_id, matchday=3)
    assert (third["name"], third["matches"], third["is_current"]) == (
        "Achtelfinale",
        [],
        False,
    )
    assert called(aioclient_mock, "scoreboard") == 0
    assert http_calls(aioclient_mock) == 2


async def test_current_cup_round_keeps_shootouts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The current cup round comes from the coordinator, shootouts included."""
    await setup_competition(hass, aioclient_mock, freezer, "dfb")
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "dfb_matchday")
    response = await get_matchday(hass, entity_id=entity_id)
    assert response["is_current"] is True
    assert sum(1 for m in response["matches"] if m["pso"]) == 4


async def test_remove_entry_deletes_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_bl1(hass)
    await get_matchday(hass, matchday=1)
    await hass.config_entries.async_unload(entry.entry_id)
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage

"""Tests for the ESPN-only competitions (UEFA): rounds, ties, browsing, coordinator."""

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
from homeassistant.exceptions import ServiceValidationError

from .common import ESPN, load_json, standings_url, summary_url
from custom_components.matchday.const import CONF_COMPETITION, DOMAIN
from custom_components.matchday.coordinator import MatchdayCoordinator
from custom_components.matchday.logic.espn_only import (
    Round,
    annotate_ties,
    espn_match,
    pick_round,
    round_matches,
    season_rounds,
)
from custom_components.matchday.logic.matchday import INTERVAL_LIVE

# Recorded 2026-10-12 (scripts/record_uefa.py ucl --season 2026): all scoreboard
# months of uefa.champions (07/2026-06/2027) and uefa.champions_qual (06-08/2026),
# the standings after matchday 1 and the summary of one matchday 1 match.
UCL = load_json("competitions/ucl-2026-20261012.json.gz")
EVENTS = [
    (key.split("/")[0], event)
    for key, answer in UCL["scoreboards"].items()
    for event in answer.get("events") or []
]
ROUNDS = season_rounds(EVENTS)
# Recorded 2026-10-12 (scripts/record_uefa.py ucl --season 2025): the finished
# season 2025/26 with qualifying, league phase and knockout rounds, the final
# standings and the summary of the final.
UCL_2025 = load_json("competitions/ucl-2025-20261012.json.gz")
EVENTS_2025 = [
    (key.split("/")[0], event)
    for key, answer in UCL_2025["scoreboards"].items()
    for event in answer.get("events") or []
]
ROUNDS_2025 = season_rounds(EVENTS_2025)
SEASONS = {2026: UCL, 2025: UCL_2025}
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


# --- two-legged ties -----------------------------------------------------------


def round_named(rounds: list[Round], name: str) -> Round:
    return next(r for r in rounds if r.name == name)


def tie_matches(
    current: Round, events: list[tuple[str, Any]] = EVENTS_2025
) -> list[dict[str, Any]]:
    matches = round_matches(current, events, BERLIN)
    annotate_ties(matches, events)
    return matches


def sides(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    competitors = event["competitions"][0]["competitors"]
    home = next(c for c in competitors if c["homeAway"] == "home")
    away = next(c for c in competitors if c["homeAway"] == "away")
    return home, away


def test_knockout_season_is_cut_by_stage_and_leg() -> None:
    """2025/26: play-offs to semi-finals in two legs, the final alone."""
    names = [r.name for r in ROUNDS_2025]
    assert names[8:16] == [f"{n}. Spieltag" for n in range(1, 9)]
    assert names[16:] == [
        "K.-o.-Play-offs · Hinspiel",
        "K.-o.-Play-offs · Rückspiel",
        "Achtelfinale · Hinspiel",
        "Achtelfinale · Rückspiel",
        "Viertelfinale · Hinspiel",
        "Viertelfinale · Rückspiel",
        "Halbfinale · Hinspiel",
        "Halbfinale · Rückspiel",
        "Finale",
    ]
    # The semi-finals span April and May; each leg is one round.
    semis = [r for r in ROUNDS_2025 if r.stage == "semifinals"]
    assert [(r.leg, r.first_kickoff.month, len(r.events)) for r in semis] == [
        (1, 4, 2),
        (2, 5, 2),
    ]
    assert ROUNDS_2025[-1].leg is None
    assert ROUNDS_2025[-1].as_matchday()["phase"] == "final"


def _event(event_id: str, day: int, leg: int) -> dict[str, Any]:
    return {
        "id": event_id,
        "season": {"slug": "second-round"},
        "competitions": [
            {
                "date": f"2026-07-{day:02d}T18:00Z",
                "leg": {"value": leg},
                "competitors": [],
            }
        ],
    }


def test_legs_split_although_kickoffs_bridge_the_gap() -> None:
    """Conference League 2026: a first leg on the weekend between both legs."""
    days = [(21, 1), (23, 1), (25, 1), (28, 2), (30, 2)]
    rounds = season_rounds(
        [("q", _event(str(i), day, leg)) for i, (day, leg) in enumerate(days)]
    )
    assert [(r.name, len(r.events)) for r in rounds] == [
        ("2. Qualifikationsrunde · Hinspiel", 3),
        ("2. Qualifikationsrunde · Rückspiel", 2),
    ]


def test_second_legs_match_espn_aggregate_and_winner() -> None:
    """Every second leg of 2025/26 (qualifying and knockout) equals ESPN's tie."""
    by_id = {str(e["id"]): e for _slug, e in EVENTS_2025}
    checked = 0
    for current in ROUNDS_2025:
        if current.leg != 2:
            continue
        for match in tie_matches(current):
            home, away = sides(by_id[match["espn_id"]])
            aggregate = match["aggregate"]
            assert aggregate["first_leg"] is not None
            assert aggregate["score"] == (
                f"{int(home['aggregateScore'])}:{int(away['aggregateScore'])}"
            )
            winner = "team1" if home.get("advance") else "team2"
            assert aggregate["advances"] == winner
            checked += 1
    assert checked == 68


def test_tie_decided_on_penalties() -> None:
    """Hamrun - Zalgiris: 2:2 on aggregate, Hamrun through 11-10 on penalties."""
    current = round_named(ROUNDS_2025, "1. Qualifikationsrunde · Rückspiel")
    match = next(m for m in tie_matches(current) if "Hamrun" in m["team1"]["teamName"])
    assert match["aggregate"]["score"] == "2:2"
    assert match["aggregate"]["advances"] == "team1"
    assert match["pso"] == {"team1": 11.0, "team2": 10.0, "winner": "team1"}


def _first_leg_of(match: dict[str, Any]) -> tuple[int, int]:
    """The first leg's score from the second leg's team1/team2 side."""
    for _slug, event in EVENTS_2025:
        competition = event["competitions"][0]
        home, away = sides(event)
        if (
            (competition.get("leg") or {}).get("value") == 1
            and home["team"]["id"] == match["espn_away_team_id"]
            and away["team"]["id"] == match["espn_home_team_id"]
        ):
            return int(away["score"]), int(home["score"])
    raise AssertionError("no first leg")


def _second_leg_events(
    state: str, score: tuple[int, int] | None
) -> tuple[Round, list[tuple[str, Any]], str]:
    """The 2025/26 round-of-16 second legs, one of them set to ``state``."""
    current = round_named(ROUNDS_2025, "Achtelfinale · Rückspiel")
    events = copy.deepcopy(EVENTS_2025)
    _slug, event = next((s, e) for s, e in events if str(e["id"]) in current.events)
    competition = event["competitions"][0]
    competition["status"]["type"].update(
        state=state, completed=False, name="STATUS_SECOND_HALF"
    )
    competition.pop("series", None)
    for competitor, goals in zip(sides(event), score or (None, None), strict=True):
        competitor.pop("aggregateScore", None)
        competitor.pop("advance", None)
        competitor["score"] = None if goals is None else str(goals)
    return current, events, str(event["id"])


def test_running_second_leg_adds_the_live_score() -> None:
    current, events, event_id = _second_leg_events("in", (1, 0))
    match = next(m for m in tie_matches(current, events) if m["espn_id"] == event_id)
    first = _first_leg_of(match)
    assert match["live_score"] == "1:0"
    assert match["aggregate"] == {
        "first_leg": f"{first[0]}:{first[1]}",
        "score": f"{first[0] + 1}:{first[1]}",
        "advances": None,
    }


def test_second_leg_before_kickoff_shows_the_first_leg() -> None:
    current, events, event_id = _second_leg_events("pre", None)
    match = next(m for m in tie_matches(current, events) if m["espn_id"] == event_id)
    first = f"{_first_leg_of(match)[0]}:{_first_leg_of(match)[1]}"
    assert match["status"] == "pre"
    assert match["aggregate"] == {"first_leg": first, "score": first, "advances": None}


def test_aggregate_from_espn_without_the_first_leg() -> None:
    """The first legs are unknown: ESPN's aggregateScore of the second leg."""
    current = round_named(ROUNDS_2025, "Viertelfinale · Rückspiel")
    events = [
        (slug, event)
        for slug, event in EVENTS_2025
        if (event["competitions"][0].get("leg") or {}).get("value") != 1
    ]
    for match in tie_matches(current, events):
        assert match["aggregate"]["first_leg"] is None
        assert match["aggregate"]["score"]
        assert match["aggregate"]["advances"] in ("team1", "team2")


def test_single_matches_and_first_legs_have_no_aggregate() -> None:
    league = tie_matches(round_named(ROUNDS_2025, "1. Spieltag"))
    assert {(m["phase"], m["leg"], m["aggregate"]) for m in league} == {
        ("league-phase", None, None)
    }
    first_legs = tie_matches(round_named(ROUNDS_2025, "Achtelfinale · Hinspiel"))
    assert {(m["phase"], m["leg"], m["aggregate"]) for m in first_legs} == {
        ("round-of-16", 1, None)
    }


def test_final_shootout_from_the_scoreboard() -> None:
    """Paris - Arsenal 1:1, Paris win 4-3 on penalties: no summary needed."""
    (final,) = tie_matches(ROUNDS_2025[-1])
    assert (final["live_score"], final["leg"], final["aggregate"]) == (
        "1:1",
        None,
        None,
    )
    assert final["pso"] == {"team1": 4.0, "team2": 3.0, "winner": "team1"}


# --- coordinator ------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def berlin_time_zone(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")


def mock_ucl(
    aioclient_mock: AiohttpClientMocker,
    scoreboards: dict[str, Any] | None = None,
    *,
    seasons: tuple[int, ...] = (2026,),
) -> None:
    """Season scoreboards, standings and summaries (the recorded one, 404 else).

    ``scoreboards`` replaces the answers of the first season.
    """
    aioclient_mock.clear_requests()
    for index, season in enumerate(seasons):
        bundle = SEASONS[season]
        answers = bundle["scoreboards"]
        if index == 0 and scoreboards is not None:
            answers = scoreboards
        for key, answer in answers.items():
            slug, month = key.split("/")
            aioclient_mock.get(f"{ESPN}/{slug}/scoreboard?dates={month}", json=answer)
        aioclient_mock.get(
            standings_url("uefa.champions", season), json=bundle["standings"]
        )
        for key, answer in bundle["scoreboards"].items():
            slug = key.split("/")[0]
            for event in answer.get("events") or []:
                event_id = str(event["id"])
                if event_id in bundle["summaries"]:
                    aioclient_mock.get(
                        summary_url(slug, event_id), json=bundle["summaries"][event_id]
                    )
                else:
                    aioclient_mock.get(
                        summary_url(slug, event_id), status=HTTPStatus.NOT_FOUND
                    )


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


async def test_scoreboards_ask_for_all_events(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without limit ESPN cuts a month at 100 events."""
    freezer.move_to("2026-10-12T13:00:00Z")
    mock_ucl(aioclient_mock)
    await setup_ucl(hass)
    scoreboards = [
        str(call[1])
        for call in aioclient_mock.mock_calls
        if "scoreboard" in str(call[1])
    ]
    assert scoreboards
    assert all("limit=500" in url for url in scoreboards)


async def test_knockout_week_shows_second_legs_with_aggregate(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """17.03.2026: round-of-16 second legs, first legs from the season."""
    freezer.move_to("2026-03-17T12:00:00Z")
    mock_ucl(aioclient_mock, seasons=(2025,))
    entry = await setup_ucl(hass)
    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get(MATCHDAY_ENTITY)
    assert state is not None
    assert state.state == "Achtelfinale · Rückspiel"
    assert state.attributes["phase"] == "round-of-16"
    assert state.attributes["season"] == 2025
    matches = state.attributes["matches"]
    assert len(matches) == 8
    assert all(m["leg"] == 2 and m["aggregate"]["first_leg"] for m in matches)
    assert [d["name"] for d in state.attributes["matchdays"]][-1] == "Finale"


# --- browsing -------------------------------------------------------------------


async def call(hass: HomeAssistant, service: str, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        service,
        {"entity_id": MATCHDAY_ENTITY, **data},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
    return response


async def test_browse_a_settled_round_of_the_season(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Matchday 1 from the season's events: no further request."""
    freezer.move_to("2026-10-12T13:00:00Z")
    mock_ucl(aioclient_mock)
    await setup_ucl(hass)
    aioclient_mock.mock_calls.clear()

    response = await call(hass, "get_matchday", matchday=9)
    assert (response["name"], response["matchday"], response["season"]) == (
        "1. Spieltag",
        9,
        2026,
    )
    assert response["is_current"] is False
    assert len(response["matches"]) == 18
    assert all(m["status"] == "post" for m in response["matches"])
    assert called(aioclient_mock, "scoreboard") == 0

    current = await call(hass, "get_matchday")
    assert (current["name"], current["is_current"]) == ("2. Spieltag", True)

    with pytest.raises(ServiceValidationError):
        await call(hass, "get_matchday", matchday=99)


async def test_browse_a_round_around_now_fetches_it_fresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """After the mid-break switch, the last round is fetched fresh (15 min cache)."""
    freezer.move_to("2026-10-17T18:00:00Z")
    mock_ucl(aioclient_mock)
    await setup_ucl(hass)
    assert hass.states.get(MATCHDAY_ENTITY).state == "3. Spieltag"
    aioclient_mock.mock_calls.clear()

    # The recording has matchday 2 not played yet: not settled, not future.
    first = await call(hass, "get_matchday", matchday=10)
    assert first["name"] == "2. Spieltag"
    assert called(aioclient_mock, "uefa.champions/scoreboard?dates=202610") == 1
    assert await call(hass, "get_matchday", matchday=10) == first
    assert called(aioclient_mock, "uefa.champions/scoreboard?dates=202610") == 1


async def test_browse_another_season_with_ties(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Season 2025: fetched once (15 scoreboards), second legs with aggregate."""
    freezer.move_to("2026-10-12T13:00:00Z")
    mock_ucl(aioclient_mock, seasons=(2026, 2025))
    await setup_ucl(hass)
    aioclient_mock.mock_calls.clear()

    number = round_named(ROUNDS_2025, "Achtelfinale · Rückspiel").number
    response = await call(hass, "get_matchday", matchday=number, season=2025)
    assert response["name"] == "Achtelfinale · Rückspiel"
    assert all(m["aggregate"]["advances"] for m in response["matches"])
    assert called(aioclient_mock, "scoreboard") == 15

    final = await call(hass, "get_matchday", matchday=len(ROUNDS_2025), season=2025)
    assert final["matches"][0]["pso"]["winner"] == "team1"
    assert called(aioclient_mock, "scoreboard") == 15


async def test_match_details_of_another_round(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The summary comes from the match's slug, once; unknown ids are refused."""
    freezer.move_to("2026-10-12T13:00:00Z")
    mock_ucl(aioclient_mock)
    await setup_ucl(hass)
    aioclient_mock.mock_calls.clear()

    (recorded,) = UCL["summaries"]
    details = await call(hass, "get_match_details", match_id=int(recorded))
    assert details["stats"]
    assert details["lineups"]
    assert called(aioclient_mock, f"uefa.champions/summary?event={recorded}") == 1
    assert await call(hass, "get_match_details", match_id=int(recorded)) == details
    assert called(aioclient_mock, "/summary") == 1

    with pytest.raises(ServiceValidationError):
        await call(hass, "get_match_details", match_id=1)

"""All phase-2 competitions against their recorded APIs and template sensors.

For each competition the coordinator runs on the answers recorded on 2026-09-27
(``scripts/record_fixtures.py --template-sensors``) and its sensors are compared
with ``scripts/compare_shadow.py`` against the Jinja template sensors recorded at
the same time. Expected differences are spelled out per competition.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import (
    ESPN,
    OLDB,
    load_bundle,
    schedule_answers,
    standings_url,
    summary_url,
)
from custom_components.matchday.const import COMPETITIONS, CONF_COMPETITION, DOMAIN
from custom_components.matchday.crests import CRESTS

SCRIPT = Path(__file__).parents[1] / "scripts" / "compare_shadow.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compare_shadow", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
async def berlin_time_zone(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")


def mock_bundle(aioclient_mock: AiohttpClientMocker, bundle: dict[str, Any]) -> None:
    """Serve the recorded answers of a bundle."""
    league, slug, season = bundle["league"], bundle["slug"], bundle["season"]
    answers: dict[str, Any] = {
        f"{OLDB}/getlastmatchbyleagueshortcut/{league}": bundle["lastmatch"],
        f"{OLDB}/getnextmatchbyleagueshortcut/{league}": bundle["nextmatch"],
        f"{OLDB}/getmatchdata/{league}/{season}/{bundle['matchday']}": bundle[
            "matches"
        ],
        **schedule_answers(league, season),
    }
    for month, scoreboard in bundle["scoreboards"].items():
        answers[f"{ESPN}/{slug}/scoreboard?dates={month}"] = scoreboard
    for event_id, summary in bundle["summaries"].items():
        answers[summary_url(slug, event_id)] = summary
    if bundle["standings"] is not None:
        answers[standings_url(slug, season)] = bundle["standings"]
        answers[f"{OLDB}/getbltable/{league}/{season}"] = bundle["oldb_table"]
    aioclient_mock.clear_requests()
    for url, data in answers.items():
        aioclient_mock.get(url, json=data)


async def setup_competition(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    key: str,
) -> tuple[dict[str, Any], MockConfigEntry]:
    bundle = load_bundle(key)
    freezer.move_to(datetime.fromisoformat(bundle["recorded"]))
    mock_bundle(aioclient_mock, bundle)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=COMPETITIONS[key].name,
        unique_id=key,
        data={CONF_COMPETITION: key},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return bundle, entry


def state_of(hass: HomeAssistant, key: str, kind: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{key}_{kind}"
    )
    return hass.states.get(entity_id) if entity_id else None


def as_state(state: Any) -> dict[str, Any]:
    return {"state": state.state, "attributes": dict(state.attributes)}


def called(aioclient_mock: AiohttpClientMocker, fragment: str) -> int:
    return sum(fragment in str(call[1]) for call in aioclient_mock.mock_calls)


# Summary columns: the template sensors had lost their summary data on
# 2026-09-27 (window closed, prev fallback gone after a restart), Matchday
# fetches every finished match once.
SUMMARY_COLUMNS = {"subs", "ht", "stats", "lineups", "pso"}


@pytest.mark.parametrize(
    ("key", "matches", "shootouts", "scorer_order"),
    [
        ("bl1", 9, 0, 0),
        ("bl2", 9, 0, 0),
        # Four cup games went to penalties. ESPN lists the shootout kicks
        # first; Matchday sorts by minute (121'+ after 90'), Jinja keeps ESPN's
        # order: same scorers, other order (three games; for Großaspach -
        # Bielefeld ESPN already lists the shootout last).
        ("dfb", 32, 4, 3),
        ("pl", 10, 0, 0),
    ],
)
async def test_matchday_against_template_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    script: ModuleType,
    key: str,
    matches: int,
    shootouts: int,
    scorer_order: int,
) -> None:
    """Pairing, status, score, scorers and cards equal the template sensor."""
    bundle, _entry = await setup_competition(hass, aioclient_mock, freezer, key)
    state = state_of(hass, key, "matchday")
    template = bundle["template_sensors"]["matchday"]

    assert state.state == template["state"]
    shown = state.attributes["matches"]
    assert len(shown) == matches
    assert all(match["espn_id"] for match in shown)

    report, _deviating = script.compare(template, as_state(state))
    assert f"matches: old {matches}, new {matches}" in report
    old = {m["matchID"]: m for m in template["attributes"]["matches"]}
    changed = Counter(
        field
        for match in shown
        for field, diff in script.FIELDS.items()
        if field not in SUMMARY_COLUMNS and diff(old[match["matchID"]], match)
    )
    assert changed == (Counter(scorers=scorer_order) if scorer_order else {}), report
    # Every finished match now has its summary details.
    assert all(m["ht_score"] and m["lineups"] and m["stats"] for m in shown)
    assert sum(1 for m in shown if m["pso"]) == shootouts


async def test_cup_shootout_scorers_sorted_by_minute(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Saarbrücken - Hertha: goals first, then the shootout, plus ``pso``."""
    await setup_competition(hass, aioclient_mock, freezer, "dfb")
    match = next(
        m
        for m in state_of(hass, "dfb", "matchday").attributes["matches"]
        if m["matchID"] == 81849
    )
    assert [s["minute"] for s in match["scorers"]][:2] == ["69'", "90'+2'"]
    assert match["pso"] == {"team1": 2.0, "team2": 4.0, "winner": "team2"}


@pytest.mark.parametrize("key", ["bl1", "bl2", "pl"])
async def test_table_against_template_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    script: ModuleType,
    key: str,
) -> None:
    """The table equals ``sensor.<liga>_tabelle_live`` in every field."""
    bundle, _entry = await setup_competition(hass, aioclient_mock, freezer, key)
    state = state_of(hass, key, "table")
    template = bundle["template_sensors"]["table_live"]

    _report, deviating = script.compare_table(template, as_state(state))
    assert deviating == 0
    # The recording has the OpenLigaDB crests, ours come from CRESTS.
    expected = [
        {**row, "teamIconUrl": ours["teamIconUrl"]}
        if ours["teamIconUrl"] in CRESTS.values()
        else row
        for row, ours in zip(
            template["attributes"]["table"], state.attributes["table"], strict=True
        )
    ]
    assert state.attributes["table"] == expected
    assert state.attributes["live"] is False
    assert state.state == bundle["recorded"].replace("Z", "+00:00")


async def test_summaries_are_fetched_once(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Long finished matches: one summary each at setup, none afterwards."""
    bundle, entry = await setup_competition(hass, aioclient_mock, freezer, "bl2")
    assert called(aioclient_mock, "/summary") == 9
    assert called(aioclient_mock, "/standings") == 1

    mock_bundle(aioclient_mock, bundle)
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success
    assert called(aioclient_mock, "/summary") == 0
    # Standings are fresh (30 min outside the matchday).
    assert called(aioclient_mock, "/standings") == 0


async def test_premier_league_drops_openligadb_placeholders(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """``matchResults`` is emptied like in the Jinja block; ESPN has the score."""
    await setup_competition(hass, aioclient_mock, freezer, "pl")
    matches = state_of(hass, "pl", "matchday").attributes["matches"]
    assert all(match["matchResults"] == [] for match in matches)
    assert all(match["live_score"] for match in matches)


async def test_cup_has_no_table(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """DFB-Pokal: no table sensor, no standings request."""
    await setup_competition(hass, aioclient_mock, freezer, "dfb")
    assert state_of(hass, "dfb", "table") is None
    assert called(aioclient_mock, "/standings") == 0
    assert called(aioclient_mock, "getbltable") == 0


@pytest.mark.parametrize(
    ("key", "matchday", "table"),
    [
        (
            "bl1",
            "sensor.matchday_bundesliga_spieltag",
            "sensor.matchday_bundesliga_tabelle",
        ),
        (
            "bl2",
            "sensor.matchday_2_bundesliga_spieltag",
            "sensor.matchday_2_bundesliga_tabelle",
        ),
        ("dfb", "sensor.matchday_dfb_pokal_spieltag", None),
        (
            "pl",
            "sensor.matchday_premier_league_spieltag",
            "sensor.matchday_premier_league_tabelle",
        ),
    ],
)
async def test_german_entity_ids_match_compare_script(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    script: ModuleType,
    key: str,
    matchday: str,
    table: str | None,
) -> None:
    """The entity ids on a German system are those compare_shadow.py expects."""
    hass.config.language = "de"
    await setup_competition(hass, aioclient_mock, freezer, key)
    assert hass.states.get(matchday) is not None
    if table:
        assert hass.states.get(table) is not None
    entities = script.COMPETITIONS[key]
    assert (entities.new_matchday, entities.new_table) == (matchday, table)

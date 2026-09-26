"""Tests for the matchday sensor entity."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .test_coordinator import mock_sources, setup_bl1
from custom_components.matchday.const import CONF_COMPETITION, DOMAIN
from custom_components.matchday.sensor import MatchdaySensor

ENTITY_ID = "sensor.matchday_bundesliga_matchday"


@pytest.fixture(autouse=True)
def recorded_break(
    aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """Serve the recorded answers on 2026-09-26 (break after matchday 4)."""
    freezer.move_to("2026-09-26T10:00:00Z")
    mock_sources(aioclient_mock)


async def test_state_and_attributes(hass: HomeAssistant) -> None:
    """State is the matchday name, attributes follow the template format."""
    await setup_bl1(hass)
    state = hass.states.get(ENTITY_ID)

    assert state.state == "4. Spieltag"
    assert state.attributes["matchday"] == 4
    assert state.attributes["season"] == 2026
    assert state.attributes["competition"] == "bl1"
    assert state.attributes["friendly_name"] == "Matchday Bundesliga Matchday"
    matches = state.attributes["matches"]
    assert [m["matchID"] for m in matches][:2] == [83183, 83186]
    expected_keys = {
        "matchID",
        "matchDateTime",
        "matchDateTimeUTC",
        "group",
        "team1",
        "team2",
        "matchIsFinished",
        "matchResults",
        "goals",
        "oldb_score",
        "espn_id",
        "espn_home_team_id",
        "espn_away_team_id",
        "minute",
        "live_score",
        "scorers",
        "cards",
        "status",
        "substitutions",
        "stats",
        "lineups",
        "ht_score",
    }
    assert expected_keys <= set(matches[0])


async def test_matches_are_not_recorded() -> None:
    """``matches`` exceeds the recorder's attribute limit and stays unrecorded."""
    assert "matches" in MatchdaySensor._unrecorded_attributes


async def test_device_and_unique_id(hass: HomeAssistant) -> None:
    """One service device per competition."""
    await setup_bl1(hass)
    entity = er.async_get(hass).async_get(ENTITY_ID)
    assert entity.unique_id == "bl1_matchday"
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.name == "Matchday Bundesliga"
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_german_entity_id_does_not_collide(hass: HomeAssistant) -> None:
    """In German the entity becomes sensor.matchday_bundesliga_spieltag.

    The template sensor it shadows is sensor.bundesliga_spieltag.
    """
    hass.config.language = "de"
    await setup_bl1(hass)
    state = hass.states.get("sensor.matchday_bundesliga_spieltag")
    assert state is not None
    assert state.state == "4. Spieltag"
    assert hass.states.get("sensor.bundesliga_spieltag") is None


async def test_unload(hass: HomeAssistant) -> None:
    """The entry unloads cleanly."""
    entry = await setup_bl1(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_espn_only_competition_is_not_supported_yet(hass: HomeAssistant) -> None:
    """UEFA competitions fail setup with a clear error until phase 3."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UEFA Champions League",
        unique_id="ucl",
        data={CONF_COMPETITION: "ucl"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR

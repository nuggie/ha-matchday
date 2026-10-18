"""Tests for the match push blueprint: an automation built from the YAML file."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
import shutil
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component

BLUEPRINT = (
    Path(__file__).parents[1]
    / "blueprints"
    / "automation"
    / "matchday"
    / "match_push.yaml"
)

type Setup = Callable[..., Awaitable[list[ServiceCall]]]


def event_data(**extra: Any) -> dict[str, Any]:
    """Return the fields of a match event (as ``logic/events.py`` sends them)."""
    return {
        "competition": "bl1",
        "match_id": 1,
        "espn_id": "401",
        "team1": "SC Freiburg",
        "team2": "FC Bayern München",
        "team1_short": "SCF",
        "team2_short": "FCB",
        "score": "2:1",
        "minute": "67'",
        "status": "live",
        **extra,
    }


@pytest.fixture
async def setup_push(hass: HomeAssistant, tmp_path: Path) -> Setup:
    """Return a function that sets up one automation from the blueprint."""
    hass.config.config_dir = str(tmp_path)
    folder = tmp_path / "blueprints" / "automation" / "matchday"
    folder.mkdir(parents=True)
    shutil.copy(BLUEPRINT, folder)
    calls = async_mock_service(hass, "test", "notify")

    async def setup(**inputs: Any) -> list[ServiceCall]:
        action = {
            "action": "test.notify",
            "data": {"message": "{{ message }}", "event": "{{ event }}"},
        }
        config = {
            "use_blueprint": {
                "path": "matchday/match_push.yaml",
                "input": {"actions": [action], **inputs},
            }
        }
        assert await async_setup_component(hass, "automation", {"automation": config})
        await hass.async_block_till_done()
        return calls

    return setup


async def fire(hass: HomeAssistant, kind: str, **extra: Any) -> None:
    hass.bus.async_fire(f"matchday_{kind}", event_data(**extra))
    await hass.async_block_till_done()


def messages(calls: list[ServiceCall]) -> list[str]:
    return [call.data["message"] for call in calls]


async def test_goal_push_for_the_team(hass: HomeAssistant, setup_push: Setup) -> None:
    calls = await setup_push(teams=["SC Freiburg"])
    await fire(hass, "goal", team="team1", scorer="Grifo", goal_type="regular")
    await fire(
        hass, "goal", score="2:2", team="team2", scorer="Kane", goal_type="penalty"
    )
    await fire(
        hass, "goal", score="3:2", team="team1", scorer="Upamecano", goal_type="owngoal"
    )
    await fire(
        hass,
        "goal",
        score="4:2",
        minute="80'",
        team="team1",
        scorer=None,
        goal_type=None,
    )
    assert messages(calls) == [
        "⚽ TOR! 67' — SC Freiburg 2:1 FC Bayern München (Grifo)",
        "⚽ TOR! 67' — SC Freiburg 2:2 FC Bayern München (Kane, Elfmeter)",
        "⚽ TOR! 67' — SC Freiburg 3:2 FC Bayern München (Upamecano, Eigentor)",
        "⚽ TOR! 80' — SC Freiburg 4:2 FC Bayern München",
    ]
    assert [call.data["event"] for call in calls] == ["goal"] * 4


async def test_other_teams_are_left_out(hass: HomeAssistant, setup_push: Setup) -> None:
    calls = await setup_push(teams=["  sc freiburg ", "SC Freiburg"])
    await fire(hass, "goal", team1="Hamburger SV", team2="1. FC Köln", scorer="X")
    await fire(hass, "goal", team1="1. FC Köln", team2="SC Freiburg", scorer="Grifo")
    assert messages(calls) == ["⚽ TOR! 67' — 1. FC Köln 2:1 SC Freiburg (Grifo)"]


async def test_no_teams_means_all_matches(
    hass: HomeAssistant, setup_push: Setup
) -> None:
    calls = await setup_push()
    await fire(hass, "goal", team1="Hamburger SV", team2="1. FC Köln", scorer="X")
    assert len(calls) == 1


async def test_default_events_leave_out_the_kickoff(
    hass: HomeAssistant, setup_push: Setup
) -> None:
    calls = await setup_push(teams=["SC Freiburg"])
    await fire(hass, "kickoff", score="0:0", minute="1'")
    await fire(hass, "goal_disallowed", score="1:1", scorer="Grifo")
    await fire(hass, "final_whistle", status="post")
    assert [call.data["event"] for call in calls] == [
        "goal_disallowed",
        "final_whistle",
    ]


async def test_kickoff_disallowed_and_final(
    hass: HomeAssistant, setup_push: Setup
) -> None:
    """Only the chosen events: the goal in between is left out."""
    calls = await setup_push(
        teams=["SC Freiburg"],
        events=["kickoff", "goal_disallowed", "final_whistle"],
    )
    await fire(hass, "kickoff", score="0:0", minute="1'")
    await fire(hass, "goal", scorer="Grifo")
    await fire(
        hass,
        "goal_disallowed",
        score="1:1",
        team="team1",
        scorer="Grifo",
        goal_type="regular",
    )
    await fire(
        hass, "final_whistle", status="post", ht_score="1:0", pso=None, aggregate=None
    )
    await fire(
        hass,
        "final_whistle",
        score="1:1",
        status="post",
        ht_score=None,
        pso={"team1": 4.0, "team2": 3.0, "winner": "team1"},
        aggregate={"first_leg": "0:1", "score": "2:2", "advances": "team1"},
    )
    assert messages(calls) == [
        "▶️ Anpfiff — SC Freiburg – FC Bayern München",
        "❌ Tor zurückgenommen — SC Freiburg 1:1 FC Bayern München (Grifo)",
        "🏁 Endstand: SC Freiburg 2:1 FC Bayern München (HZ 1:0)",
        "🏁 Endstand: SC Freiburg 1:1 FC Bayern München (i.E. 4:3, Gesamt 2:2)",
    ]


async def test_english_messages(hass: HomeAssistant, setup_push: Setup) -> None:
    calls = await setup_push(
        events=["kickoff", "goal", "goal_disallowed", "final_whistle"], language="en"
    )
    await fire(hass, "kickoff", score="0:0", minute="1'")
    await fire(hass, "goal", scorer="Kane", goal_type="owngoal")
    await fire(hass, "goal_disallowed", scorer=None, goal_type=None)
    await fire(hass, "final_whistle", ht_score="1:0", pso={"team1": 5, "team2": 4})
    assert messages(calls) == [
        "▶️ Kick-off — SC Freiburg – FC Bayern München",
        "⚽ GOAL! 67' — SC Freiburg 2:1 FC Bayern München (Kane, own goal)",
        "❌ Goal disallowed — SC Freiburg 2:1 FC Bayern München",
        "🏁 Full time: SC Freiburg 2:1 FC Bayern München (HT 1:0, pens 5:4)",
    ]

"""Tests for the shadow comparison script (scripts/compare_shadow.py)."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from .common import load_json
from custom_components.matchday.logic.merge import merge_matches, norm_de

SCRIPT = Path(__file__).parents[1] / "scripts" / "compare_shadow.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compare_shadow", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state() -> dict[str, Any]:
    matches = merge_matches(
        load_json("oldb_bl1_2026_4.json"),
        load_json("espn_ger1_202609.json")["events"],
        norm_de,
        ZoneInfo("Europe/Berlin"),
    )
    return {"state": "4. Spieltag", "attributes": {"matches": matches}}


def test_identical_sensors(script: ModuleType, state: dict[str, Any]) -> None:
    """No deviations for equal data."""
    report, deviating = script.compare(state, copy.deepcopy(state))
    assert deviating == 0
    assert "0 with deviations" in report


def test_deviations_are_reported(script: ModuleType, state: dict[str, Any]) -> None:
    """Changed fields, reordered scorers and missing matches are counted."""
    new = copy.deepcopy(state)
    matches = new["attributes"]["matches"]
    matches[0]["status"] = "live"
    matches[0]["scorers"].reverse()
    matches[1]["cards"] = []
    del matches[2]

    report, deviating = script.compare(state, new)
    assert deviating == 3
    assert "post->live" in report
    assert "order" in report
    assert "only old" in report


def test_plausibility(script: ModuleType, state: dict[str, Any]) -> None:
    """The single-sensor summary counts ESPN coverage."""
    summary = script.plausibility(state)
    assert "with espn_id: 9/9" in summary
    assert "status: post=9" in summary

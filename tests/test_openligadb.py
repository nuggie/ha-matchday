"""Tests for the OpenLigaDB client helpers."""

from custom_components.matchday.api.openligadb import (
    CREST_OVERRIDES,
    _as_match,
    _fix_crest,
)

DEAD = "https://www.bundesliga-reisefuehrer.de/sites/default/files/B04_Standard_Logo_RGB.png"


def test_dead_crest_is_replaced_in_matches() -> None:
    """A known-dead crest is replaced for either team, others stay untouched."""
    match = _as_match(
        {
            "matchDateTimeUTC": "2026-10-17T13:30:00Z",
            "group": {"groupOrderID": 7},
            "team1": {"teamId": 6, "teamIconUrl": DEAD},
            "team2": {"teamId": 16, "teamIconUrl": "https://i.imgur.com/v0tkpNx.png"},
        }
    )
    assert match is not None
    assert match["team1"]["teamIconUrl"] == CREST_OVERRIDES[6]
    assert match["team2"]["teamIconUrl"] == "https://i.imgur.com/v0tkpNx.png"


def test_dead_crest_is_replaced_in_table() -> None:
    """The table uses ``teamInfoId`` instead of ``teamId``."""
    team = _fix_crest({"teamInfoId": 6, "teamIconUrl": DEAD}, "teamInfoId")
    assert team["teamIconUrl"] == CREST_OVERRIDES[6]

"""Tests for the OpenLigaDB client helpers."""

from custom_components.matchday.api.openligadb import _apply_crest, _as_match
from custom_components.matchday.crests import CRESTS

DEAD = "https://www.bundesliga-reisefuehrer.de/sites/default/files/B04_Standard_Logo_RGB.png"


def test_crests_win_in_matches() -> None:
    """Both teams get the crest from CRESTS, a team without entry keeps its URL."""
    match = _as_match(
        {
            "matchDateTimeUTC": "2026-10-17T13:30:00Z",
            "group": {"groupOrderID": 7},
            "team1": {"teamId": 6, "teamIconUrl": DEAD},
            "team2": {"teamId": 16, "teamIconUrl": "https://i.imgur.com/v0tkpNx.png"},
        }
    )
    assert match is not None
    assert match["team1"]["teamIconUrl"] == CRESTS[6]
    assert match["team2"]["teamIconUrl"] == CRESTS[16]

    amateur = {"teamId": 999999, "teamIconUrl": "https://example.org/crest.png"}
    assert _apply_crest(amateur)["teamIconUrl"] == "https://example.org/crest.png"


def test_crests_win_in_table() -> None:
    """The table uses ``teamInfoId`` instead of ``teamId``."""
    team = _apply_crest({"teamInfoId": 6, "teamIconUrl": DEAD}, "teamInfoId")
    assert team["teamIconUrl"] == CRESTS[6]


def test_crests_are_wikimedia_https() -> None:
    """Every crest is an https URL on Wikimedia, never inline data."""
    assert CRESTS
    assert all(
        url.startswith("https://upload.wikimedia.org/") for url in CRESTS.values()
    )

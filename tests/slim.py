"""Slim an ESPN match summary to the paths Matchday reads.

The replay and competition fixtures in ``tests/fixtures/`` store summaries in this form;
``test_summary.py`` checks that the slimming keeps everything the parser needs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# The ESPN statistics Matchday shows (logic/summary.py STAT_KEYS).
SUMMARY_STATS = ("possessionPct", "totalShots", "shotsOnTarget", "wonCorners")


def _pick(data: Any, keys: Sequence[str]) -> dict[str, Any]:
    """Return the ``keys`` present in ``data`` (a dict), else an empty dict."""
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in keys if key in data}


def slim_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Reduce an ESPN summary to the paths Matchday reads.

    Kept: competitors of ``header`` (score, line scores, shootout, winner) and
    the status type, ``keyEvents`` (type, clock, team, participant names), the
    four statistics of ``boxscore.teams`` and ``rosters`` (formation, starter
    flag and name of each player). The clock of the status is dropped on
    purpose: without it, summaries that differ only in the minute compare equal.
    """
    competitions = (summary.get("header") or {}).get("competitions") or []
    header_competitions = [
        {
            "status": {
                "type": _pick((c.get("status") or {}).get("type"), ("state", "name"))
            },
            "competitors": [
                {
                    **_pick(
                        competitor,
                        ("homeAway", "score", "winner", "shootoutScore", "linescores"),
                    ),
                    "team": _pick(competitor.get("team"), ("id", "displayName")),
                }
                for competitor in c.get("competitors") or []
            ],
        }
        for c in competitions[:1]
    ]
    key_events = [
        {
            "type": _pick(event.get("type"), ("type",)),
            "clock": _pick(event.get("clock"), ("value", "displayValue")),
            "team": _pick(event.get("team"), ("id",)),
            "participants": [
                {"athlete": _pick(p.get("athlete"), ("displayName",))}
                for p in event.get("participants") or []
            ],
        }
        for event in summary.get("keyEvents") or []
    ]
    box_teams = [
        {
            **_pick(team, ("homeAway",)),
            "team": _pick(team.get("team"), ("id", "displayName")),
            "statistics": [
                _pick(stat, ("name", "displayValue"))
                for stat in team.get("statistics") or []
                if stat.get("name") in SUMMARY_STATS
            ],
        }
        for team in (summary.get("boxscore") or {}).get("teams") or []
    ]
    rosters = [
        {
            **_pick(roster, ("homeAway", "formation")),
            "roster": [
                {
                    **_pick(player, ("starter",)),
                    "athlete": _pick(player.get("athlete"), ("displayName",)),
                }
                for player in roster.get("roster") or []
            ],
        }
        for roster in summary.get("rosters") or []
    ]
    return {
        "header": {"competitions": header_competitions},
        "keyEvents": key_events,
        "boxscore": {"teams": box_teams},
        "rosters": rosters,
    }

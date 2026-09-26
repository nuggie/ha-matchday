"""Tests for matchday selection, ESPN window and polling interval."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from .common import load_json, oldb_match
from custom_components.matchday.api.espn import scoreboard_months
from custom_components.matchday.logic.matchday import (
    INTERVAL_IDLE,
    INTERVAL_LIVE,
    INTERVAL_WINDOW,
    MatchdayPick,
    any_live,
    default_season,
    pick_matchday,
    round_window,
    should_query_espn,
    update_interval,
)

LAST = load_json("oldb_bl1_lastmatch.json")  # matchday 4, 2026-09-20 17:30 UTC
NEXT = load_json("oldb_bl1_nextmatch.json")  # matchday 5, 2026-10-09 18:30 UTC
# Halfway between both kickoffs: 2026-09-30 06:00 UTC.
MIDPOINT = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_utc(2026, 9, 21, 12), 4),  # day after the matchday
        (_utc(2026, 9, 26, 12), 4),  # break, last matchday still closer
        (MIDPOINT - timedelta(minutes=1), 4),
        (MIDPOINT, 4),  # a tie keeps the last matchday
        (MIDPOINT + timedelta(minutes=1), 5),
        (_utc(2026, 10, 8, 12), 5),
    ],
)
def test_closer_matchday_wins_during_break(now: datetime, expected: int) -> None:
    """The display switches to the next matchday in the middle of the break."""
    assert pick_matchday(LAST, NEXT, now) == MatchdayPick(expected, 2026)


def test_season_start_has_no_last_match() -> None:
    """Before the first match of a season only the next match is known."""
    assert pick_matchday(None, NEXT, _utc(2026, 7, 20)) == MatchdayPick(5, 2026)


def test_season_end_has_no_next_match() -> None:
    """After the last match of a season only the last match is known."""
    assert pick_matchday(LAST, None, _utc(2027, 6, 10)) == MatchdayPick(4, 2026)


def test_season_comes_from_the_chosen_match() -> None:
    """The season of the chosen match wins over the calendar."""
    last = oldb_match(1, "2027-05-22T13:30:00Z", "A", "B", group=34, season=2026)
    nxt = oldb_match(2, "2027-08-20T18:30:00Z", "C", "D", group=1, season=2027)
    assert pick_matchday(last, nxt, _utc(2027, 5, 30)) == MatchdayPick(34, 2026)
    assert pick_matchday(last, nxt, _utc(2027, 8, 10)) == MatchdayPick(1, 2027)


def test_missing_season_falls_back_to_calendar() -> None:
    """Without ``leagueSeason`` the season is derived from the date."""
    last = {**LAST, "leagueSeason": None}
    assert pick_matchday(last, None, _utc(2027, 3, 1)) == MatchdayPick(4, 2026)


def test_api_failure_keeps_previous_matchday() -> None:
    """Neither last nor next match known: keep what is shown."""
    previous = MatchdayPick(7, 2026)
    assert pick_matchday(None, None, _utc(2026, 10, 30), previous) == previous


def test_api_failure_without_previous_starts_at_one() -> None:
    """Nothing known at all: matchday 1 of the calendar season."""
    assert pick_matchday(None, None, _utc(2026, 8, 1)) == MatchdayPick(1, 2026)
    assert pick_matchday(None, None, _utc(2027, 2, 1)) == MatchdayPick(1, 2026)


def test_broken_match_objects_are_ignored() -> None:
    """A match without group or kickoff counts as missing."""
    broken_last = {**LAST, "group": None}
    broken_next = {**NEXT, "matchDateTimeUTC": "not a date"}
    assert pick_matchday(broken_last, NEXT, _utc(2026, 9, 21)) == MatchdayPick(5, 2026)
    assert pick_matchday(LAST, broken_next, _utc(2026, 10, 8)) == MatchdayPick(4, 2026)


@pytest.mark.parametrize(
    ("now", "season"),
    [(_utc(2026, 6, 30), 2025), (_utc(2026, 7, 1), 2026), (_utc(2027, 1, 15), 2026)],
)
def test_default_season(now: datetime, season: int) -> None:
    """Seasons start in July."""
    assert default_season(now) == season


def test_round_window_uses_first_and_last_date() -> None:
    """The window spans the dates of the matchday's matches."""
    matches = load_json("oldb_bl1_2026_4.json")
    assert round_window(matches) == (date(2026, 9, 18), date(2026, 9, 20))
    assert round_window([]) is None


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 9, 16), False),  # two days before
        (date(2026, 9, 17), True),  # window opens a day before
        (date(2026, 9, 19), True),
        (date(2026, 9, 22), True),  # window closes two days after
        (date(2026, 9, 26), True),  # round_past: matchday still shown
    ],
)
def test_should_query_espn(today: date, expected: bool) -> None:
    """ESPN is queried around the matchday and while it is still shown after it."""
    window = (date(2026, 9, 18), date(2026, 9, 20))
    assert should_query_espn(window, today) is expected


def test_any_live() -> None:
    """Live and half time count as running."""
    assert any_live([{"status": "post"}, {"status": "ht"}])
    assert any_live([{"status": "live"}])
    assert not any_live([{"status": "pre"}, {"status": "post"}])


NOW = _utc(2026, 9, 19, 13, 25)
WINDOW = (date(2026, 9, 18), date(2026, 9, 20))


@pytest.mark.parametrize(
    ("match", "today", "window", "expected"),
    [
        ({"status": "live"}, date(2026, 9, 19), WINDOW, INTERVAL_LIVE),
        ({"status": "ht"}, date(2026, 9, 19), WINDOW, INTERVAL_LIVE),
        # kicks off in 5 minutes
        (
            {"status": "pre", "matchDateTimeUTC": "2026-09-19T13:30:00Z"},
            date(2026, 9, 19),
            WINDOW,
            INTERVAL_LIVE,
        ),
        # kicked off 20 minutes ago, ESPN still says pre
        (
            {"status": "pre", "matchDateTimeUTC": "2026-09-19T13:05:00Z"},
            date(2026, 9, 19),
            WINDOW,
            INTERVAL_LIVE,
        ),
        # kicks off in 3 hours
        (
            {"status": "pre", "matchDateTimeUTC": "2026-09-19T16:30:00Z"},
            date(2026, 9, 19),
            WINDOW,
            INTERVAL_WINDOW,
        ),
        ({"status": "post"}, date(2026, 9, 22), WINDOW, INTERVAL_WINDOW),
        ({"status": "post"}, date(2026, 9, 26), WINDOW, INTERVAL_IDLE),
        ({"status": "pre"}, date(2026, 9, 10), WINDOW, INTERVAL_IDLE),
        ({"status": "pre"}, date(2026, 9, 10), None, INTERVAL_IDLE),
    ],
)
def test_update_interval(
    match: dict, today: date, window: tuple[date, date] | None, expected: timedelta
) -> None:
    """Fast while live or about to start, medium around the matchday, slow else."""
    assert update_interval([match], window, today, NOW) == expected


@pytest.mark.parametrize(
    ("first", "last", "months"),
    [
        (date(2026, 9, 18), date(2026, 9, 20), ["202609"]),
        (date(2026, 9, 30), date(2026, 10, 2), ["202609", "202610"]),
        (date(2026, 12, 30), date(2027, 1, 2), ["202612", "202701"]),
    ],
)
def test_scoreboard_months(first: date, last: date, months: list[str]) -> None:
    """Month syntax: first month, plus the last one across a month boundary."""
    assert scoreboard_months(first, last) == months

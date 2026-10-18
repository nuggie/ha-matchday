"""Match events from successive updates: kickoff, goal, goal disallowed, final whistle.

The coordinator hands every update's ``matches`` to ``EventTracker.update``, which
compares them with what it knew of each match and returns the events to fire.

Goals follow the **score**, not ``details``: ESPN lists a new goal in the details
up to 30 s before it raises the score, and drops an annulled goal up to 30 s after
it lowered the score (recording 2026-09-26). A side's n-th goal is announced when
its score reaches n; the scorer is the side's n-th scoring play (ESPN credits an
own goal to the side it counts for, the athlete is the opponent's player;
shootout kicks sort after all goals and are never reached, the score leaves the
shootout out). If that scoring play is not there yet, the match's new goals wait
one update for it, then go out without a scorer. A lowered score takes back the
side's last announced goals, with the scorer they were announced with.

Events need a match observed before: the first update after a restart, on a new
matchday or for a match ESPN did not know before only records its state, as does
a match paired with another ESPN event than before. A match without ESPN data in
an update keeps its state, so a pairing that drops out for one update does not
announce its score again. Goals and disallowed goals are only
announced while a match runs and in the update of its final whistle; kickoff and
final whistle once per match. A running match that ESPN suddenly reports as not
started is ignored for that update (it would take back every goal).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from .merge import minute_sort_key

type EventKind = Literal["kickoff", "goal", "goal_disallowed", "final_whistle"]
type Side = Literal["team1", "team2"]

EVENT_KINDS: tuple[EventKind, ...] = (
    "kickoff",
    "goal",
    "goal_disallowed",
    "final_whistle",
)
SIDES: tuple[Side, Side] = ("team1", "team2")
RUNNING = frozenset({"live", "ht"})
STATUSES = frozenset({"pre", "live", "ht", "post"})
# How long the state of a match no update mentions any more is kept.
KEEP = timedelta(hours=6)
# Sort key of a goal whose scorer is not known: after all known ones.
_UNKNOWN_MINUTE = (10**6, 0, 0.0)


@dataclass(frozen=True, slots=True)
class MatchEvent:
    """An event to fire: ``kind`` becomes the event type ``matchday_<kind>``."""

    kind: EventKind
    data: dict[str, Any]


@dataclass(slots=True)
class _MatchState:
    espn_id: Any
    status: str
    # Announced goals per side, oldest first: the scoring play (``scorers``
    # entry) each was announced with, ``None`` if it had none.
    goals: dict[Side, list[Mapping[str, Any] | None]]
    seen: datetime
    kicked_off: bool
    finished: bool
    # The match's new goals already waited one update for their scorer.
    held: bool = False

    def score(self) -> tuple[int, int]:
        return len(self.goals["team1"]), len(self.goals["team2"])


@dataclass(slots=True)
class EventTracker:
    """Remember each match between updates and derive its events."""

    _matches: dict[Any, _MatchState] = field(default_factory=dict)

    def update(
        self,
        matches: Iterable[Mapping[str, Any]],
        now: datetime,
        context: Mapping[str, Any],
    ) -> list[MatchEvent]:
        """Return the events of this update; ``context`` goes into every event."""
        events: list[MatchEvent] = []
        for match in matches:
            observed = _observe(match)
            if observed is None:
                continue
            key, status, score, plays = observed
            state = self._matches.get(key)
            if state is None or state.espn_id != match["espn_id"]:
                goals = _sync(score, plays)
                self._matches[key] = _MatchState(
                    espn_id=match["espn_id"],
                    status=status,
                    goals=goals,
                    seen=now,
                    kicked_off=status != "pre",
                    finished=status == "post",
                )
                continue
            state.seen = now
            events.extend(
                MatchEvent(kind, {**context, **data})
                for kind, data in _match_events(state, match, status, score, plays)
            )
        for key in [k for k, state in self._matches.items() if now - state.seen > KEEP]:
            del self._matches[key]
        return events


def _observe(
    match: Mapping[str, Any],
) -> tuple[Any, str, tuple[int, int], dict[Side, list[Mapping[str, Any]]]] | None:
    """Return (key, status, score, scoring plays per side) of a match with ESPN data."""
    key = match.get("matchID")
    status = match.get("status")
    if key is None or not match.get("espn_id") or status not in STATUSES:
        return None
    score = (0, 0) if status == "pre" else _parse_score(match.get("live_score"))
    if score is None:
        return None
    plays: dict[Side, list[Mapping[str, Any]]] = {side: [] for side in SIDES}
    for entry in match.get("scorers") or []:
        if isinstance(entry, Mapping) and entry.get("team") in plays:
            plays[entry["team"]].append(entry)
    return key, str(status), score, plays


def _parse_score(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    home, away = value.split(":", 1)
    try:
        return int(home), int(away)
    except ValueError:
        return None


def _sync(
    score: tuple[int, int], plays: Mapping[Side, Sequence[Mapping[str, Any]]]
) -> dict[Side, list[Mapping[str, Any] | None]]:
    """Return the goals of ``score`` as if announced, with the known scorers."""
    goals: dict[Side, list[Mapping[str, Any] | None]] = {}
    for side, count in zip(SIDES, score, strict=True):
        known: list[Mapping[str, Any] | None] = [dict(p) for p in plays[side][:count]]
        goals[side] = known + [None] * (count - len(known))
    return goals


def _match_events(
    state: _MatchState,
    match: Mapping[str, Any],
    status: str,
    score: tuple[int, int],
    plays: Mapping[Side, Sequence[Mapping[str, Any]]],
) -> list[tuple[EventKind, dict[str, Any]]]:
    was = state.status
    if status == "pre" and was != "pre":
        return []
    events: list[tuple[EventKind, dict[str, Any]]] = []
    if was == "pre" and status in RUNNING and not state.kicked_off:
        state.kicked_off = True
        events.append(("kickoff", _base(match, state, status)))

    final = was in RUNNING and status == "post" and not state.finished
    if was in RUNNING or (was == "pre" and status in RUNNING):
        events.extend(_goal_events(state, match, status, score, plays, flush=final))
    else:
        state.goals = _sync(score, plays)
        state.held = False

    if final:
        state.finished = True
        events.append(
            (
                "final_whistle",
                {
                    **_base(match, state, status),
                    "ht_score": match.get("ht_score"),
                    "pso": match.get("pso"),
                    "aggregate": match.get("aggregate"),
                },
            )
        )
    state.status = status
    return events


def _goal_events(
    state: _MatchState,
    match: Mapping[str, Any],
    status: str,
    score: tuple[int, int],
    plays: Mapping[Side, Sequence[Mapping[str, Any]]],
    *,
    flush: bool,
) -> list[tuple[EventKind, dict[str, Any]]]:
    events: list[tuple[EventKind, dict[str, Any]]] = []
    for side, count in zip(SIDES, score, strict=True):
        while len(state.goals[side]) > count:
            play = state.goals[side].pop()
            events.append(
                ("goal_disallowed", _goal_data(match, state, status, side, play))
            )

    new = [
        (side, plays[side][index] if index < len(plays[side]) else None)
        for side, count in zip(SIDES, score, strict=True)
        for index in range(len(state.goals[side]), count)
    ]
    if not new:
        state.held = False
        return events
    if not flush and not state.held and any(play is None for _side, play in new):
        state.held = True
        return events
    state.held = False
    # Stable: goals of one side keep their order, unknown scorers come last.
    for side, play in sorted(new, key=lambda item: _play_order(item[1])):
        state.goals[side].append(dict(play) if play is not None else None)
        events.append(("goal", _goal_data(match, state, status, side, play)))
    return events


def _play_order(play: Mapping[str, Any] | None) -> tuple[int, int, float]:
    if play is None:
        return _UNKNOWN_MINUTE
    try:
        value = float(play.get("sortKey") or 0)
    except (TypeError, ValueError):
        value = 0.0
    return minute_sort_key(str(play.get("minute") or ""), value)


def _team(match: Mapping[str, Any], side: Side) -> Mapping[str, Any]:
    team = match.get(side)
    return team if isinstance(team, Mapping) else {}


def _base(match: Mapping[str, Any], state: _MatchState, status: str) -> dict[str, Any]:
    """Return the fields every event carries; ``score`` as announced so far."""
    team1, team2 = _team(match, "team1"), _team(match, "team2")
    home, away = state.score()
    return {
        "match_id": match.get("matchID"),
        "espn_id": match.get("espn_id"),
        "team1": team1.get("teamName", ""),
        "team2": team2.get("teamName", ""),
        "team1_short": team1.get("shortName", ""),
        "team2_short": team2.get("shortName", ""),
        "score": f"{home}:{away}",
        "minute": match.get("minute"),
        "status": status,
        "kickoff": match.get("matchDateTimeUTC"),
    }


def _goal_data(
    match: Mapping[str, Any],
    state: _MatchState,
    status: str,
    side: Side,
    play: Mapping[str, Any] | None,
) -> dict[str, Any]:
    data = _base(match, state, status)
    if play is not None and play.get("minute"):
        data["minute"] = play["minute"]
    data.update(
        {
            "team": side,
            "team_name": _team(match, side).get("teamName", ""),
            "scorer": (play.get("name") or None) if play is not None else None,
            "goal_type": play.get("type") if play is not None else None,
        }
    )
    return data

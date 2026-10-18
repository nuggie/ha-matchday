"""Replay harness: drive the coordinator through a recorded ESPN matchday.

``tests/fixtures/replay/*.json.gz`` hold real ESPN scoreboard snapshots of live
games (``scripts/make_replay_fixture.py``). The harness pairs them with a
synthetic OpenLigaDB season in which the recorded games form one matchday, serves
every source according to the simulated clock (freezer) and advances the clock in
steps of the ``update_interval`` the coordinator sets itself. The scheduled
refresh is fired by Home Assistant's own timer, so a wrong interval shows up as a
missing or early update. Where the recording changes between two scheduled
refreshes (goal, status, card), the harness adds a refresh at that moment, as a
user pressing "update" would; the coordinator reschedules from there.

Match summaries are served for the game recorded with summaries (ESP @ ENG):
the last one recorded before the simulated time. The recorder stored summaries
only while a game ran, so the final one is from 90'+6'. Summary requests for
other games (or before the first recording) answer HTTP 404.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import gzip
import json
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .common import ESPN, FIXTURES, OLDB, oldb_match, schedule_answers
from custom_components.matchday.const import CONF_COMPETITION, DOMAIN, Competition
from custom_components.matchday.coordinator import MatchdayCoordinator
from custom_components.matchday.logic.events import EVENT_KINDS

REPLAY_FIXTURE = FIXTURES / "replay" / "uefa.nations-20260926.json.gz"

# A test-only competition: OpenLigaDB has no Nations League, the synthetic
# "unl" league stands in for it. English team names, like ESPN's.
REPLAY_COMPETITION = Competition(
    "unl",
    "Nations League Replay",
    "uefa.nations",
    "unl",
    team_names="en",
    has_table=False,
)
SEASON = 2026
MATCHDAY = 3

# OpenLigaDB match id -> (ESPN short name, home, away, kickoff UTC).
REPLAY_MATCHES: dict[int, tuple[str, str, str, str]] = {
    3001: ("SCO @ SVN", "Slovenia", "Scotland", "2026-09-26T13:00:00Z"),
    3002: ("CRO @ CZE", "Czechia", "Croatia", "2026-09-26T18:45:00Z"),
    3003: ("ESP @ ENG", "England", "Spain", "2026-09-26T18:45:00Z"),
}
# Neighbouring matchdays, far enough away that matchday 3 is shown from the
# middle of the break before it until the middle of the break after it.
PREVIOUS_MATCH = oldb_match(
    2001, "2026-09-08T18:45:00Z", "Scotland", "Greece", group=2, finished=True
)
NEXT_MATCH = oldb_match(4001, "2026-10-10T16:00:00Z", "England", "Latvia", group=4)
# OpenLigaDB enters the final score a while after the final whistle.
OLDB_RESULT_DELAY = timedelta(minutes=15)


def parse_ts(value: str) -> datetime:
    """Parse a fixture timestamp (``2026-09-26T12:30:01Z``)."""
    return datetime.fromisoformat(value).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One recorded scoreboard, valid from ``ts`` until the next snapshot."""

    ts: datetime
    events: tuple[dict[str, Any], ...]

    def event(self, short_name: str) -> dict[str, Any]:
        """Return the ESPN event with ``short_name`` ("ESP @ ENG")."""
        return next(e for e in self.events if e["shortName"] == short_name)


def competition_of(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``competitions[0]`` of an ESPN event."""
    return event["competitions"][0]


def espn_score(event: Mapping[str, Any]) -> str:
    """Return the recorded score of an event as "home:away"."""
    sides = {c["homeAway"]: c["score"] for c in competition_of(event)["competitors"]}
    return f"{sides['home']}:{sides['away']}"


def espn_status(event: Mapping[str, Any]) -> tuple[str, str]:
    """Return (state, type name) of an event, e.g. ("in", "STATUS_HALFTIME")."""
    status_type = competition_of(event)["status"]["type"]
    return status_type["state"], status_type["name"]


class Recording:
    """The snapshots of a replay fixture, looked up by (simulated) time."""

    def __init__(self, path: Path = REPLAY_FIXTURE) -> None:
        """Load the fixture."""
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        self.slug: str = data["slug"]
        self.snapshots = [
            Snapshot(parse_ts(snap["ts"]), tuple(snap["events"]))
            for snap in data["scoreboards"]
        ]
        self._times = [snap.ts for snap in self.snapshots]
        self.summaries: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
        for item in data.get("summaries") or []:
            self.summaries.setdefault(item["event"], []).append(
                (parse_ts(item["ts"]), item["summary"])
            )

    def at(self, now: datetime) -> Snapshot:
        """Return the snapshot valid at ``now`` (the first one before it starts)."""
        return self.snapshots[max(bisect_right(self._times, now) - 1, 0)]

    def changes(self) -> list[datetime]:
        """Return the times at which status, score or details of a game change.

        Clock-only changes are left out: those the regular refreshes sample.
        """

        def key(snapshot: Snapshot) -> list[Any]:
            return [
                (
                    event["id"],
                    espn_status(event),
                    espn_score(event),
                    competition_of(event).get("details"),
                )
                for event in snapshot.events
            ]

        return [
            current.ts
            for previous, current in zip(self.snapshots, self.snapshots[1:])
            if key(previous) != key(current)
        ]

    def summary_at(self, event_id: str, now: datetime) -> dict[str, Any] | None:
        """Return the last summary of ``event_id`` recorded up to ``now``."""
        recorded = self.summaries.get(event_id) or []
        times = [ts for ts, _summary in recorded]
        index = bisect_right(times, now) - 1
        return recorded[index][1] if index >= 0 else None

    def final_whistle(self, short_name: str) -> datetime:
        """Return when ESPN first reported ``short_name`` as finished."""
        return next(
            snap.ts
            for snap in self.snapshots
            if espn_status(snap.event(short_name))[0] == "post"
        )


@dataclass(slots=True)
class MatchView:
    """What the sensor shows of one match at one refresh."""

    status: str
    minute: str | None
    live_score: str | None
    scorers: list[tuple[str, str, str]]
    cards: list[tuple[str, str, str]]
    espn_id: str | None
    oldb_score: str | None
    substitutions: int
    ht_score: str | None
    stats: dict[str, list[int]] | None
    lineups: bool


@dataclass(slots=True)
class Observation:
    """The state after one refresh of the replay."""

    time: datetime
    trigger: str  # "setup", "scheduled" or "extra"
    success: bool
    interval: timedelta
    matchday: int
    sensor_state: str
    lookups: int  # last/next match requests made by this refresh
    espn_requests: int
    summary_requests: dict[str, int]  # ESPN event id -> requests by this refresh
    snapshot: Snapshot
    matches: dict[str, MatchView] = field(default_factory=dict)
    # live_score per match as the sensor entity shows it.
    sensor_scores: dict[str, str | None] = field(default_factory=dict)
    # Match events fired by this refresh, in order.
    events: list[FiredEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FiredEvent:
    """A match event, with the score the sensor showed when it was fired."""

    event_type: str
    game: str
    data: dict[str, Any]
    sensor_score: str | None


def _view(match: Mapping[str, Any]) -> MatchView:
    def entries(items: Iterable[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
        return [(item["minute"], item["name"], item["team"]) for item in items]

    return MatchView(
        status=match["status"],
        minute=match["minute"],
        live_score=match["live_score"],
        scorers=entries(match["scorers"]),
        cards=entries(match["cards"]),
        espn_id=match["espn_id"],
        oldb_score=match["oldb_score"],
        substitutions=len(match["substitutions"]),
        ht_score=match["ht_score"],
        stats=match["stats"],
        lineups=match["lineups"] is not None,
    )


class Replay:
    """Run the coordinator of the replay competition against a recording."""

    def __init__(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        freezer: FrozenDateTimeFactory,
        recording: Recording,
    ) -> None:
        """Register the time-dependent source answers."""
        self.hass = hass
        self.freezer = freezer
        self.recording = recording
        self.requests: list[str] = []
        # Lets a test make OpenLigaDB point somewhere else (matchday lock test).
        self.lookup_override: dict[str, Any] | None = None
        self.observations: list[Observation] = []
        self.entry: MockConfigEntry | None = None
        self._entity_id: str | None = None
        self._fired: list[FiredEvent] = []
        for kind in EVENT_KINDS:
            hass.bus.async_listen(f"{DOMAIN}_{kind}", self._on_event)
        self._labels = {match_id: spec[0] for match_id, spec in REPLAY_MATCHES.items()}
        self._results_at = {
            match_id: recording.final_whistle(short_name) + OLDB_RESULT_DELAY
            for match_id, (short_name, *_rest) in REPLAY_MATCHES.items()
        }

        league = REPLAY_COMPETITION.openligadb_shortcut
        answers: dict[str, Callable[[], Any]] = {
            f"{OLDB}/getlastmatchbyleagueshortcut/{league}": self._last_match,
            f"{OLDB}/getnextmatchbyleagueshortcut/{league}": self._next_match,
            f"{OLDB}/getmatchdata/{league}/{SEASON}/{MATCHDAY}": self._matchday,
            f"{OLDB}/getmatchdata/{league}/{SEASON}/{MATCHDAY - 1}": lambda: [
                PREVIOUS_MATCH
            ],
            f"{OLDB}/getmatchdata/{league}/{SEASON}/{MATCHDAY + 1}": lambda: [
                NEXT_MATCH
            ],
            # The coordinator asks for the month, the recorder asked for the day.
            f"{ESPN}/{recording.slug}/scoreboard?dates=202609": self._scoreboard,
        }
        aioclient_mock.clear_requests()
        for url, answer in answers.items():
            aioclient_mock.get(url, side_effect=self._responder(answer))
        for event_id in self._event_ids():
            aioclient_mock.get(
                f"{ESPN}/{recording.slug}/summary?event={event_id}",
                side_effect=self._summary_responder(event_id),
            )
        # The season schedule (matchdays attribute) is not part of the replay.
        for url, answer in schedule_answers(league, SEASON).items():
            aioclient_mock.get(url, json=answer)

    def _event_ids(self) -> set[str]:
        return {
            str(event["id"])
            for snapshot in self.recording.snapshots
            for event in snapshot.events
        }

    def _responder(self, answer: Callable[[], Any]) -> Callable[[str, URL, Any], Any]:
        async def respond(
            method: str, url: URL, data: Any
        ) -> AiohttpClientMockResponse:
            self.requests.append(str(url))
            return AiohttpClientMockResponse(method, url, json=answer())

        return respond

    def _summary_responder(self, event_id: str) -> Callable[[str, URL, Any], Any]:
        async def respond(
            method: str, url: URL, data: Any
        ) -> AiohttpClientMockResponse:
            self.requests.append(str(url))
            summary = self.recording.summary_at(event_id, dt_util.utcnow())
            if summary is None:
                return AiohttpClientMockResponse(method, url, status=404)
            return AiohttpClientMockResponse(method, url, json=summary)

        return respond

    @callback
    def _on_event(self, event: Event) -> None:
        state = self.hass.states.get(self._entity_id) if self._entity_id else None
        shown = next(
            (
                match["live_score"]
                for match in (state.attributes["matches"] if state else [])
                if match["matchID"] == event.data["match_id"]
            ),
            None,
        )
        self._fired.append(
            FiredEvent(
                event.event_type,
                self._labels.get(event.data["match_id"], ""),
                dict(event.data),
                shown,
            )
        )

    # --- synthetic OpenLigaDB -------------------------------------------------

    def _matchday(self) -> list[dict[str, Any]]:
        now = dt_util.utcnow()
        matches = []
        for match_id, (short_name, home, away, kickoff) in REPLAY_MATCHES.items():
            finished = now >= self._results_at[match_id]
            result = None
            if finished:
                final = self.recording.at(now).event(short_name)
                result = tuple(int(goals) for goals in espn_score(final).split(":"))
            matches.append(
                {
                    **oldb_match(
                        match_id,
                        kickoff,
                        home,
                        away,
                        group=MATCHDAY,
                        season=SEASON,
                        finished=finished,
                        result=result,
                    ),
                    "leagueShortcut": REPLAY_COMPETITION.openligadb_shortcut,
                }
            )
        return matches

    def _season(self) -> list[dict[str, Any]]:
        return sorted(
            [PREVIOUS_MATCH, *self._matchday(), NEXT_MATCH],
            key=lambda match: match["matchDateTimeUTC"],
        )

    def _last_match(self) -> dict[str, Any] | None:
        if self.lookup_override is not None:
            return self.lookup_override
        now = dt_util.utcnow()
        started = [m for m in self._season() if parse_ts(m["matchDateTimeUTC"]) <= now]
        return started[-1] if started else None

    def _next_match(self) -> dict[str, Any] | None:
        if self.lookup_override is not None:
            return self.lookup_override
        now = dt_util.utcnow()
        coming = [m for m in self._season() if parse_ts(m["matchDateTimeUTC"]) > now]
        return coming[0] if coming else None

    # --- ESPN -------------------------------------------------------------------

    def _scoreboard(self) -> dict[str, Any]:
        return {"events": list(self.recording.at(dt_util.utcnow()).events)}

    # --- driving the clock ------------------------------------------------------

    @property
    def coordinator(self) -> MatchdayCoordinator:
        """Return the coordinator of the replay config entry."""
        assert self.entry is not None
        return self.entry.runtime_data

    @property
    def now(self) -> datetime:
        """Return the simulated time."""
        return dt_util.utcnow()

    async def async_setup(self, start: datetime) -> Observation:
        """Set the clock to ``start`` and set up the config entry."""
        self.freezer.move_to(start)
        self.entry = MockConfigEntry(
            domain=DOMAIN,
            title=REPLAY_COMPETITION.name,
            unique_id=REPLAY_COMPETITION.key,
            data={CONF_COMPETITION: REPLAY_COMPETITION.key},
        )
        self.entry.add_to_hass(self.hass)
        mark = len(self.requests)
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()
        self._entity_id = er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{REPLAY_COMPETITION.key}_matchday"
        )
        return self._observe("setup", mark)

    async def async_step(self) -> Observation:
        """Advance by the coordinator's interval and let its timer refresh."""
        mark = len(self.requests)
        self.freezer.tick(self.coordinator.update_interval)
        async_fire_time_changed(self.hass)
        # The scheduled refresh runs as a background task: wait for it, or a
        # refresh that awaits several requests is observed half done.
        await self.hass.async_block_till_done(wait_background_tasks=True)
        assert len(self.requests) > mark, f"no scheduled refresh at {self.now}"
        return self._observe("scheduled", mark)

    async def async_refresh_at(self, when: datetime) -> Observation:
        """Move the clock to ``when`` (before the next refresh) and refresh."""
        mark = len(self.requests)
        self.freezer.move_to(when)
        await self.coordinator.async_refresh()
        await self.hass.async_block_till_done()
        return self._observe("extra", mark)

    async def async_run_until(
        self, end: datetime, extra_at: Sequence[datetime] = ()
    ) -> list[Observation]:
        """Replay until ``end``: scheduled refreshes plus refreshes at ``extra_at``."""
        pending = sorted(t for t in extra_at if t > self.now)
        while self.now < end:
            scheduled = self.now + self.coordinator.update_interval
            while pending and pending[0] <= self.now:
                pending.pop(0)
            if pending and pending[0] < scheduled:
                await self.async_refresh_at(pending.pop(0))
            else:
                await self.async_step()
        return self.observations

    def _label(self, match: Mapping[str, Any]) -> str:
        """Return the ESPN short name of a replayed match, else its id."""
        return self._labels.get(match["matchID"], str(match["matchID"]))

    def _observe(self, trigger: str, mark: int) -> Observation:
        new = self.requests[mark:]
        coordinator = self.coordinator
        data = coordinator.data
        state = self.hass.states.get(self._entity_id) if self._entity_id else None
        observation = Observation(
            time=self.now,
            trigger=trigger,
            success=coordinator.last_update_success,
            interval=coordinator.update_interval,
            matchday=data.matchday,
            sensor_state=state.state if state else "",
            lookups=sum("matchbyleagueshortcut" in url for url in new),
            espn_requests=sum("/scoreboard" in url for url in new),
            summary_requests=dict(
                Counter(url.rsplit("=", 1)[1] for url in new if "/summary" in url)
            ),
            snapshot=self.recording.at(self.now),
            matches={self._label(match): _view(match) for match in data.matches},
            sensor_scores={
                self._label(match): match["live_score"]
                for match in (state.attributes["matches"] if state else [])
            },
            events=self._fired,
        )
        self._fired = []
        self.observations.append(observation)
        return observation


def collapse[T](values: Iterable[T]) -> list[T]:
    """Drop consecutive repeats: [a, a, b, a] -> [a, b, a]."""
    result: list[T] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result

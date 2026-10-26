"""Compare a Matchday sensor state with the Jinja template sensor it replaced.

Test helper for the golden tests in ``test_competitions.py``: Matchday was developed in shadow
mode next to the author's former template sensors, and the recorded fixtures still carry their
state. Matchday: the ``matches`` attribute per OpenLigaDB ``matchID`` (ESPN id, status, live
score, kickoff, scorers, cards and the summary fields). Table (leagues only): position, points,
matches played and live flag per team (ESPN team id).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any, NamedTuple


class Entities(NamedTuple):
    """Template sensor and Matchday sensor of a competition (German system)."""

    old_matchday: str
    new_matchday: str
    old_table: str | None = None
    new_table: str | None = None


COMPETITIONS: dict[str, Entities] = {
    "bl1": Entities(
        "sensor.bundesliga_spieltag",
        "sensor.matchday_bundesliga_spieltag",
        "sensor.bundesliga_tabelle_live",
        "sensor.matchday_bundesliga_tabelle",
    ),
    "bl2": Entities(
        "sensor.bundesliga2_spieltag",
        "sensor.matchday_2_bundesliga_spieltag",
        "sensor.bundesliga2_tabelle_live",
        "sensor.matchday_2_bundesliga_tabelle",
    ),
    "dfb": Entities("sensor.dfbpokal_spieltag", "sensor.matchday_dfb_pokal_spieltag"),
    "pl": Entities(
        "sensor.premierleague_spieltag",
        "sensor.matchday_premier_league_spieltag",
        "sensor.premierleague_tabelle_live",
        "sensor.matchday_premier_league_tabelle",
    ),
    "ucl": Entities(
        "sensor.championsleague_spieltag",
        "sensor.matchday_uefa_champions_league_spieltag",
        "sensor.championsleague_tabelle_live",
        "sensor.matchday_uefa_champions_league_tabelle",
    ),
    "uel": Entities(
        "sensor.europaleague_spieltag",
        "sensor.matchday_uefa_europa_league_spieltag",
        "sensor.europaleague_tabelle_live",
        "sensor.matchday_uefa_europa_league_tabelle",
    ),
    "uecl": Entities(
        "sensor.conferenceleague_spieltag",
        "sensor.matchday_uefa_conference_league_spieltag",
        "sensor.conferenceleague_tabelle_live",
        "sensor.matchday_uefa_conference_league_tabelle",
    ),
}


DEFAULT_OLD = COMPETITIONS["bl1"].old_matchday


DEFAULT_NEW = COMPETITIONS["bl1"].new_matchday


Match = dict[str, Any]


def _names(items: list[dict[str, Any]] | None) -> list[str]:
    return [str(item.get("name", "")) for item in items or []]


def _cards(items: list[dict[str, Any]] | None) -> list[str]:
    return [f"{item.get('name', '')}/{item.get('type', '')}" for item in items or []]


def _list_diff(old: list[str], new: list[str]) -> str | None:
    """Describe the difference of two event lists, ``None`` if equal."""
    if old == new:
        return None
    if Counter(old) == Counter(new):
        return "order"
    return f"{len(old)}->{len(new)}"


def _kickoff(value: Any) -> Any:
    """Normalize a UTC kickoff to minutes ("...T16:45:00Z" == "...T16:45Z")."""
    if isinstance(value, str) and value.endswith(":00Z") and value.count(":") == 2:
        return value[:-4] + "Z"
    return value


def _value_diff(old: Any, new: Any) -> str | None:
    return None if old == new else f"{old}->{new}"


def _presence_diff(old: Any, new: Any) -> str | None:
    """Compare structured values: ``None`` if equal, else what changed."""
    if old == new:
        return None
    if old is None or new is None:
        return f"{'none' if old is None else 'set'}->{'none' if new is None else 'set'}"
    return "differs"


def _subs(match: Match) -> int:
    return len(match.get("substitutions") or [])


def _lineups(match: Match) -> bool | None:
    """``True`` if the match has lineups, ``None`` otherwise."""
    return True if match.get("lineups") else None


FIELDS: dict[str, Callable[[Match, Match], str | None]] = {
    "espn_id": lambda o, n: _value_diff(o.get("espn_id"), n.get("espn_id")),
    "status": lambda o, n: _value_diff(o.get("status"), n.get("status")),
    "score": lambda o, n: _value_diff(o.get("live_score"), n.get("live_score")),
    # The UEFA Jinja keeps ESPN's "2026-10-13T16:45Z", Matchday adds seconds.
    "kickoff": lambda o, n: _value_diff(
        _kickoff(o.get("matchDateTimeUTC")), _kickoff(n.get("matchDateTimeUTC"))
    ),
    "scorers": lambda o, n: _list_diff(
        _names(o.get("scorers")), _names(n.get("scorers"))
    ),
    "cards": lambda o, n: _list_diff(_cards(o.get("cards")), _cards(n.get("cards"))),
    "subs": lambda o, n: _value_diff(_subs(o), _subs(n)),
    "ht": lambda o, n: _value_diff(o.get("ht_score"), n.get("ht_score")),
    "stats": lambda o, n: _presence_diff(o.get("stats"), n.get("stats")),
    # Only whether lineups are there: the names are ESPN's either way.
    "lineups": lambda o, n: _presence_diff(_lineups(o), _lineups(n)),
    "pso": lambda o, n: _presence_diff(o.get("pso"), n.get("pso")),
}


def _pairing(match: Match) -> str:
    team1 = (match.get("team1") or {}).get("shortName") or (
        match.get("team1") or {}
    ).get("teamName", "?")
    team2 = (match.get("team2") or {}).get("shortName") or (
        match.get("team2") or {}
    ).get("teamName", "?")
    return f"{team1} - {team2}"[:30]


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)) for row in rows
    ]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)


def plausibility(state: dict[str, Any]) -> str:
    """Summarize one sensor on its own (used when there is nothing to compare)."""
    matches: list[Match] = state.get("attributes", {}).get("matches") or []
    statuses = Counter(str(m.get("status")) for m in matches)
    with_espn = sum(1 for m in matches if m.get("espn_id"))
    with_score = sum(1 for m in matches if m.get("live_score"))
    post_without_score = [
        _pairing(m)
        for m in matches
        if m.get("status") == "post" and not m.get("live_score")
    ]
    lines = [
        f"state: {state.get('state')!r}, {len(matches)} matches",
        f"with espn_id: {with_espn}/{len(matches)}, with live_score: {with_score}",
        "status: " + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())),
        (
            f"scorers total: {sum(len(m.get('scorers') or []) for m in matches)}, "
            f"cards total: {sum(len(m.get('cards') or []) for m in matches)}"
        ),
        (
            f"with ht_score: {sum(1 for m in matches if m.get('ht_score'))}, "
            f"stats: {sum(1 for m in matches if m.get('stats'))}, "
            f"lineups: {sum(1 for m in matches if m.get('lineups'))}, "
            f"substitutions total: {sum(_subs(m) for m in matches)}"
        ),
    ]
    if post_without_score:
        lines.append("finished without ESPN score: " + ", ".join(post_without_score))
    return "\n".join(lines)


# The UEFA Jinja sensors' state is the competition name, not the round.
UNCOMPARED_STATE = frozenset({"ucl", "uel", "uecl"})


def compare(old: dict[str, Any], new: dict[str, Any]) -> tuple[str, int]:
    """Return the report and the number of matches with deviations."""
    compare_state = (new.get("attributes") or {}).get("competition") not in (
        UNCOMPARED_STATE
    )
    old_matches = {m.get("matchID"): m for m in old["attributes"].get("matches") or []}
    new_matches = {m.get("matchID"): m for m in new["attributes"].get("matches") or []}
    rows = [["matchID", "match", *FIELDS]]
    deviating = 0
    field_counts: Counter[str] = Counter()
    for match_id in sorted(old_matches.keys() | new_matches.keys(), key=str):
        o, n = old_matches.get(match_id), new_matches.get(match_id)
        if o is None or n is None:
            deviating += 1
            where = "only old" if n is None else "only new"
            rows.append(
                [
                    str(match_id),
                    _pairing(o or n or {}),
                    where,
                    *[""] * (len(FIELDS) - 1),
                ]
            )
            continue
        cells = []
        for field, diff in FIELDS.items():
            result = diff(o, n)
            if result is not None:
                field_counts[field] += 1
            cells.append("=" if result is None else result)
        if any(cell != "=" for cell in cells):
            deviating += 1
        rows.append([str(match_id), _pairing(o), *cells])

    summary = [
        f"state: old {old.get('state')!r}, new {new.get('state')!r}"
        + (
            "  (UEFA Jinja: competition name, not compared)"
            if not compare_state
            else ""
            if old.get("state") == new.get("state")
            else "  <-- differs"
        ),
        (
            f"matches: old {len(old_matches)}, new {len(new_matches)}, "
            f"{deviating} with deviations"
        ),
    ]
    if field_counts:
        summary.append(
            "deviating fields: "
            + ", ".join(f"{field}={count}" for field, count in field_counts.items())
        )
    if compare_state and old.get("state") != new.get("state"):
        deviating += 1
    return _table(rows) + "\n\n" + "\n".join(summary), deviating


Team = dict[str, Any]


TABLE_FIELDS = ("pos", "points", "matches", "live")


def _team_key(team: Team) -> str:
    return str(team.get("espn_team_id") or team.get("teamName") or "?")


def table_plausibility(state: dict[str, Any]) -> str:
    """Summarize one table sensor on its own."""
    table: list[Team] = state.get("attributes", {}).get("table") or []
    live = [
        str(t.get("shortName") or t.get("teamName")) for t in table if t.get("live")
    ]
    return (
        f"state: {state.get('state')!r}, {len(table)} teams, "
        f"live: {', '.join(live) if live else '-'}"
    )


def compare_table(old: dict[str, Any], new: dict[str, Any]) -> tuple[str, int]:
    """Return the table report and the number of teams with deviations."""

    def rows(state: dict[str, Any]) -> dict[str, Team]:
        table = state["attributes"].get("table") or []
        return {
            _team_key(team): {**team, "pos": position}
            for position, team in enumerate(table, start=1)
        }

    old_rows, new_rows = rows(old), rows(new)
    report = [["team", "name", *TABLE_FIELDS]]
    deviating = 0
    field_counts: Counter[str] = Counter()
    ordered = sorted(
        old_rows.keys() | new_rows.keys(),
        key=lambda key: (old_rows.get(key) or new_rows[key])["pos"],
    )
    for key in ordered:
        o, n = old_rows.get(key), new_rows.get(key)
        name = str((o or n or {}).get("teamName", "?"))[:26]
        if o is None or n is None:
            deviating += 1
            where = "only old" if n is None else "only new"
            report.append([key, name, where, *[""] * (len(TABLE_FIELDS) - 1)])
            continue
        cells = []
        for field in TABLE_FIELDS:
            result = _value_diff(o.get(field), n.get(field))
            if result is not None:
                field_counts[field] += 1
            cells.append("=" if result is None else result)
        if any(cell != "=" for cell in cells):
            deviating += 1
        report.append([key, name, *cells])
    summary = [
        f"teams: old {len(old_rows)}, new {len(new_rows)}, {deviating} with deviations"
    ]
    if field_counts:
        summary.append(
            "deviating fields: "
            + ", ".join(f"{field}={count}" for field, count in field_counts.items())
        )
    return _table(report) + "\n\n" + "\n".join(summary), deviating

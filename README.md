# Matchday

A Home Assistant integration that shows the **current matchday** of a football competition,
with live scores, goal scorers, cards and a live table.

> **Status:** early development (phase 2 — German competitions and Premier League with match
> details and live tables, in shadow mode). Not yet published.

## What makes it different

Most football integrations are team-centric or group fixtures by calendar day. Matchday
thinks in **matchdays**: for the German competitions the fixture list and matchday grouping
come from [OpenLigaDB](https://www.openligadb.de) (real rounds, rescheduled games stay in their
matchday, German team names), while ESPN supplies live scores and match details.

## Planned competitions

| Competition | Fixtures / matchday | Live data |
|---|---|---|
| Bundesliga, 2. Bundesliga, DFB-Pokal | OpenLigaDB | ESPN |
| Premier League | OpenLigaDB (privately maintained league `pl`) | ESPN |
| Champions League, Europa League, Conference League | ESPN | ESPN |

## Current state (0.4.0)

Bundesliga, 2. Bundesliga, DFB-Pokal and Premier League (via the privately maintained
OpenLigaDB league `pl`) are supported; one config entry per competition. Each gets:

**Matchday sensor** — device `Matchday <competition>`, sensor `Matchday` / `Spieltag`
(on a German system `sensor.matchday_bundesliga_spieltag`, `sensor.matchday_2_bundesliga_spieltag`,
`sensor.matchday_dfb_pokal_spieltag`, `sensor.matchday_premier_league_spieltag`).

- **State:** the matchday or round name from OpenLigaDB, e.g. `4. Spieltag`, `1. Runde`.
- **Attributes:** `matches` (OpenLigaDB match objects enriched with ESPN data: `espn_id`,
  `status` pre/live/ht/post, `minute`, `live_score`, `scorers`, `cards`, `oldb_score`, kickoff
  corrected from ESPN, and from the match summary `substitutions`, `ht_score`, `stats`
  — possession, shots, shots on target, corners as `[home, away]` — `lineups` and `pso`
  (penalty shootout)), `matchday`, `season`, `competition`, and `matchdays` — all matchdays
  or rounds of the season, `[{"matchday": 1, "name": "1. Spieltag", "first_kickoff":
  "2026-08-28T18:30:00Z", "last_kickoff": "2026-08-30T15:30:00Z"}, …]` (kickoffs `null` for cup
  rounds without fixtures yet; refreshed every 6 hours). `matches` and `matchdays` are excluded
  from the recorder. For the Premier League `matchResults` is emptied (OpenLigaDB only has
  0:0 placeholders there).
- **Matchday choice:** by date — the last or the next match, whichever is closer in time, so
  the display switches in the middle of a break. A running match blocks the switch.
- **Polling:** 60 s while a match is live or starts within 10 minutes, 5 min around the
  matchday, 30 min otherwise. Errors keep the last good data.
- **Match summaries** (one ESPN request per match) only for running matches (every 3 minutes
  and on every status change) and finished ones (at the final whistle and once more two minutes
  later, then never again; after a restart one request per finished match). Matches that are
  not fetched keep their last details, also after the matchday window closed.

**Table sensor** (leagues only) — `Table` / `Tabelle`, e.g. `sensor.matchday_bundesliga_tabelle`.

- **State:** when the ESPN standings were fetched (timestamp).
- **Attributes:** `table` in the format of the former `sensor.<liga>_tabelle_live` template
  sensors (`teamName`, `shortName`, `teamIconUrl`, `matches`, `won`, `draw`, `lost`, `goals`,
  `opponentGoals`, `diff`, `points`, `live`, `live_score`, `espn_team_id`), `live`, `matchday`,
  `season`, `competition`.
- Numbers from the ESPN standings, names, short names and crests from OpenLigaDB
  (`getbltable`), matched by the ESPN team id of the matchday, then by name, then the previous
  table. Running matches of the matchday are added on top (**live delta**) and the table is
  re-sorted by points, goal difference, goals. A match that just ended keeps counting until
  ESPN has booked it (its `gamesPlayed` grows), so the table does not jump back after the final
  whistle.
- Standings every 30 minutes, every 10 minutes around running or just finished matches, every
  5 minutes while a finished match waits to be booked; OpenLigaDB names every 6 hours.

### Browsing matchdays (actions)

Two actions return data only (`response_variable` in scripts, `callService` with
`return_response` in cards). Both take the **matchday sensor** of a competition as `entity_id`.

**`matchday.get_matchday`** — `matchday` and `season` are optional (default: what the sensor
shows; for another season `matchday` is required).

```yaml
action: matchday.get_matchday
data:
  entity_id: sensor.matchday_bundesliga_spieltag
  matchday: 3
response_variable: result
```

```json
{"competition": "bl1", "season": 2026, "matchday": 3, "name": "3. Spieltag",
 "is_current": false, "matches": [ ... ]}
```

`matches` has exactly the format of the sensor attribute. The shown matchday comes straight
from the sensor (`is_current: true`). Other matchdays are merged from OpenLigaDB and the ESPN
scoreboard like the shown one, but without match summaries: `substitutions`, `stats`,
`lineups` and `ht_score` are empty (`[]`/`null`), `pso` (penalty shootout) is filled from the
scoreboard. Future matchdays (first kickoff more than a day away) come from OpenLigaDB alone,
all `pre`. A cup round without fixtures yet returns its name and `matches: []`.

**`matchday.get_match_details`** — the summary fields of one match, by its OpenLigaDB
`matchID`:

```yaml
action: matchday.get_match_details
data:
  entity_id: sensor.matchday_bundesliga_spieltag
  match_id: 83156
response_variable: details
```

```json
{"match_id": 83156, "substitutions": [ ... ], "stats": {"possession": [59, 41], ...},
 "lineups": {"home": {...}, "away": {...}}, "ht_score": "1:0", "pso": null}
```

Matches of the shown matchday come from the sensor; others from the ESPN summary (only for
matches ESPN knows that have started).

**Caching.** Settled matchdays (all matches finished and paired with ESPN, last kickoff more
than a day ago) and the details of settled matches are stored for good per competition
(`.storage/matchday.<entry id>`, at most 40 matchdays and 400 match details, the oldest seasons
go first; deleted with the config entry). Everything else is kept in memory for 15 minutes
(details of a running match for one minute), as are ESPN month scoreboards: browsing matchdays
2 and 3 costs one OpenLigaDB request each and one scoreboard request in total, asking again
costs nothing. Concurrent requests for the same matchday or match share one fetch. Unknown
entities, matchdays or matches raise a validation error; a failing source raises an error for
the action only — the sensors are not affected.

**Not yet:** goal/kick-off events; UEFA competitions (setting one up fails with a clear error
until phase 3); an ESPN-only Premier League mode.

`scripts/compare_shadow.py --competition bl1|bl2|dfb|pl` compares both sensors with the template
sensors they replace (`HA_TOKEN`, optional `HA_URL`; `--only matchday|table`).

## Development

```bash
uv venv --python 3.14 && uv pip install -r requirements-test.txt
.venv/bin/pytest -q
uvx ruff check custom_components tests scripts
```

Tests use recorded OpenLigaDB/ESPN answers from `tests/fixtures/`.
`scripts/record_fixtures.py <competition>` records everything the coordinator asks for on a day
(OpenLigaDB lookups, matchday, table; ESPN scoreboards, standings, slimmed summaries; with
`--template-sensors` also the template sensors to compare against) into
`tests/fixtures/competitions/<competition>-<date>.json.gz`; `tests/test_competitions.py` runs
all four competitions against them.
`scripts/record_history.py` records what matchday browsing asks for (season schedules of
bl1/dfb/pl, bl1 matchdays 1-3 and 5, cup round 2, ESPN scoreboards, one summary) into
`tests/fixtures/history/`. `scripts/check_history.py --competition bl1 --matchdays 1-4`
cross-checks browsed matchdays against the live APIs (pairing, ESPN vs OpenLigaDB final
scores).

**Live recordings and replay.** Finished matchdays cannot show how live data evolves, so
`scripts/record_espn.py` records a competition day from ESPN (every 30 s, only changed
scoreboards and live match summaries, into `recordings/`, which is not versioned):

```bash
scripts/record_espn.py --slug uefa.nations --date 20260926 --until "2026-09-26 23:15"
```

`scripts/make_replay_fixture.py --summaries` cuts a slim, reproducible fixture out of it
(selected games, only snapshots in which they change; `--game`; summaries of `--summary-game`,
slimmed to the fields Matchday reads). The committed
`tests/fixtures/replay/uefa.nations-20260926.json.gz` holds ESP @ ENG (with summaries),
CRO @ CZE (VAR) and SCO @ SVN. `tests/test_replay.py` replays it against a synthetic OpenLigaDB
matchday with a simulated clock that advances in the coordinator's own `update_interval`
(`tests/replay.py`).

## Installation

Not available through HACS yet.

## License

MIT

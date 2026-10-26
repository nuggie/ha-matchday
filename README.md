# Matchday

A Home Assistant integration that shows the **current matchday** of a football competition,
with live scores, goal scorers, cards, substitutions, statistics, line-ups and a live table —
and fires events for kickoff, goals and the final whistle.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nuggie&repository=ha-matchday&category=integration)

![Matchday Card showing a Bundesliga matchday and the live table](https://raw.githubusercontent.com/nuggie/matchday-card/main/docs/overview-desktop.png)

*Shown with the [Matchday Card](https://github.com/nuggie/matchday-card), the dashboard card made
for this integration.*

## What makes it different

Most football integrations are team-centric or group fixtures by calendar day. Matchday
thinks in **matchdays**: for the German competitions the fixture list and matchday grouping
come from [OpenLigaDB](https://www.openligadb.de) (real rounds, rescheduled games stay in their
matchday, German team names), while ESPN supplies live scores and match details.

## Competitions

| Competition | Fixtures / matchday | Live data |
|---|---|---|
| Bundesliga, 2. Bundesliga, DFB-Pokal | OpenLigaDB | ESPN |
| Premier League | OpenLigaDB (league `pl`, see [Limitations](#data-sources-and-limitations)) | ESPN |
| Champions League, Europa League, Conference League | ESPN | ESPN |

## Installation

**HACS (custom repository):** HACS → three dots → *Custom repositories* → add
`https://github.com/nuggie/ha-matchday`, category *Integration*. Install *Matchday* and restart
Home Assistant.

**Manual:** copy `custom_components/matchday/` into `config/custom_components/` and restart.

## Configuration

*Settings → Devices & services → Add integration → Matchday*, then pick the competition. Add the
integration once per competition you want to follow. Entity names follow the language of your
Home Assistant system (German or English).

For the dashboard, install the [Matchday Card](https://github.com/nuggie/matchday-card).

## Entities

**Matchday sensor** — device `Matchday <competition>`, sensor `Matchday` / `Spieltag`
(on a German system `sensor.matchday_bundesliga_spieltag`, `sensor.matchday_2_bundesliga_spieltag`,
`sensor.matchday_dfb_pokal_spieltag`, `sensor.matchday_premier_league_spieltag`, …).

- **State:** the matchday or round name, e.g. `4. Spieltag`, `1. Runde`.
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
- **Match summaries** (one ESPN request per match): from 75 minutes before kickoff every
  5 minutes until the line-ups are there, then every 15 minutes; while a match runs every
  3 minutes and on every status change; after the final whistle every 3 minutes for 10 minutes,
  then every 10 minutes until 3 hours after the whistle, then never again (a match first seen
  finished long after its kickoff, e.g. after a restart, is fetched once). Matches that are not
  fetched keep their last details, also after the matchday window closed.

**Table sensor** (leagues only) — `Table` / `Tabelle`, e.g. `sensor.matchday_bundesliga_tabelle`.

- **State:** when the ESPN standings were fetched (timestamp).
- **Attributes:** `table` — one row per team (`teamName`, `shortName`, `teamIconUrl`, `matches`,
  `won`, `draw`, `lost`, `goals`, `opponentGoals`, `diff`, `points`, `live`, `live_score`,
  `espn_team_id`) — `live`, `matchday`, `season`, `competition`.
- Numbers from the ESPN standings, names and short names from OpenLigaDB (`getbltable`),
  crests as in `matches` (see [Data sources](#data-sources-and-limitations)), matched by the ESPN team id of the matchday, then by name, then the previous
  table. Running matches of the matchday are added on top (**live delta**) and the table is
  re-sorted by points, goal difference, goals. A match that just ended keeps counting until
  ESPN has booked it (its `gamesPlayed` grows), so the table does not jump back after the final
  whistle.
- Standings every 30 minutes, every 10 minutes around running or just finished matches, every
  5 minutes while a finished match waits to be booked; OpenLigaDB names every 6 hours.

**UEFA competitions** (Champions, Europa, Conference League) run on ESPN alone. The rounds of a
season are cut from the events of all its scoreboard months (refreshed every six hours): one round
per leg of a two-legged stage (qualifying, knockout), league phase matchdays and the final by
kickoff. The shown round is picked like an OpenLigaDB matchday. Each match carries `phase` (ESPN's
stage, e.g. `league-phase`, `round-of-16`), `leg` (1, 2 or null) and, in a second leg, `aggregate`:
`first_leg` and `score` (aggregate, live while it runs) from the match's team1/team2 side and
`advances` (`team1`/`team2`) once the tie is decided; the penalty shootout (`pso`) comes straight
from the scoreboard. The matchday sensor's `phase` attribute names the stage of the shown round.
The table comes from ESPN's league phase standings.

## Actions: browsing matchdays

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
all `pre`. A cup round without fixtures yet returns its name and `matches: []`. For the UEFA
competitions the action browses the rounds of the current and earlier seasons.

**`matchday.get_match_details`** — the summary fields of one match, by its `matchID`:

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

## Match events

Every competition fires `matchday_kickoff`, `matchday_goal`, `matchday_goal_disallowed` and
`matchday_final_whistle`, right after its matchday sensor shows the change (so a triggered
automation reading the sensor sees the new state).

| Field | |
|---|---|
| `competition`, `competition_name`, `matchday`, `matchday_name` | e.g. `bl1`, `Bundesliga`, `7`, `7. Spieltag` |
| `match_id`, `espn_id`, `kickoff` | `matchID` of the `matches` attribute, ESPN event id, kickoff (UTC) |
| `team1`, `team2`, `team1_short`, `team2_short` | home and away team as the sensor names them |
| `score`, `minute`, `status` | score after the event (`2:1`), match clock, `live`/`ht`/`post` |
| `team`, `team_name`, `scorer`, `goal_type` | goals only: the side the goal counts for (`team1`/`team2`), scorer (`null` if ESPN has none yet), `regular`/`penalty`/`owngoal`; `minute` is the goal's minute |
| `ht_score`, `pso`, `aggregate` | final whistle only: half-time score, penalty shootout, aggregate of a second leg (`null` if none) |

Goals follow the **score**, not ESPN's `details`: those run up to 30 seconds ahead of the score
for a new goal and up to 30 seconds behind it for a goal taken back. The n-th goal of a side gets
the side's n-th scoring play as scorer (an own goal counts for the side it is credited to); if the
play is not there yet, the goal waits one update for it. A lowered score fires
`matchday_goal_disallowed` with the scorer the goal was announced with. Events need a match seen
before: the first update after a restart or on a new matchday only records the state, and a match
without ESPN data in an update keeps its last state. Goals are only announced while a match runs
(and in the update of its final whistle); kickoff and final whistle once per match.

## Blueprint: match push

[![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fnuggie%2Fha-matchday%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmatchday%2Fmatch_push.yaml)

`blueprints/automation/matchday/match_push.yaml` turns the events into a message for chosen
teams (or all matches): `⚽ TOR! 67' — SC Freiburg 2:1 FC Bayern München (Grifo)`,
`❌ Tor zurückgenommen — …`, `▶️ Anpfiff — …`, `🏁 Endstand: … (HZ 1:0, i.E. 4:3)`, German or
English. Inputs: `teams` (several spellings allowed, empty = all), `events` (default goal, goal
disallowed, final whistle), `language`, and the `actions` to run with `{{ message }}`, `{{ event }}`
and `{{ trigger.event.data }}`.

```yaml
use_blueprint:
  path: matchday/match_push.yaml
  input:
    teams: [SC Freiburg]
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "{{ message }}"
```

## Data sources and limitations

- **ESPN:** live scores, match details and standings come from ESPN's public site API, which is
  undocumented and can change without notice. This project is not affiliated with ESPN or
  OpenLigaDB.
- **OpenLigaDB:** a free community database; fixtures, matchday grouping and German team names
  for Bundesliga, 2. Bundesliga and DFB-Pokal.
- **Crests:** linked from the infobox of each club's Wikipedia article (`crests.py`), OpenLigaDB's
  crest URL for clubs without an entry. Crests are trademarks of their clubs; the integration
  only passes the URLs on.
- **Premier League:** OpenLigaDB has no official Premier League. The league `pl` is maintained
  there by the author of this integration (fixtures and kickoff times, synced from ESPN once a
  day); if it lags behind, the matchday grouping lags too. There is no ESPN-only Premier League
  mode yet.
- **UEFA knockout rounds** (two-legged ties, aggregate, who advances) are tested against recorded
  ESPN data of the 2025/26 season; the first live knockout round of 2026/27 is in February.

## Development

```bash
uv venv --python 3.14 && uv pip install -r requirements-test.txt
.venv/bin/pytest -q
uvx ruff check custom_components tests scripts
```

Tests use recorded OpenLigaDB/ESPN answers from `tests/fixtures/`.
`scripts/record_fixtures.py <competition>` records everything the coordinator asks for on a day
(OpenLigaDB lookups, matchday, table; ESPN scoreboards, standings, slimmed summaries) into
`tests/fixtures/competitions/<competition>-<date>.json.gz`; `tests/test_competitions.py` runs
all four competitions against them and compares the result with the template sensors Matchday
was developed to replace (recorded alongside, compared by `scripts/compare_shadow.py`).
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

Scripts that read a Home Assistant instance take `HA_TOKEN` and optional `HA_URL`
(default `http://homeassistant.local:8123`).

## License

MIT

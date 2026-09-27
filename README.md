# Matchday

A Home Assistant integration that shows the **current matchday** of a football competition,
with live scores, goal scorers, cards and a live table.

> **Status:** early development (phase 1 — Bundesliga matchday sensor in shadow mode).
> Not yet published.

## What makes it different

Most football integrations are team-centric or group fixtures by calendar day. Matchday
thinks in **matchdays**: for the German competitions the fixture list and matchday grouping
come from [OpenLigaDB](https://www.openligadb.de) (real rounds, rescheduled games stay in their
matchday, German team names), while ESPN supplies live scores and match details.

## Planned competitions

| Competition | Fixtures / matchday | Live data |
|---|---|---|
| Bundesliga, 2. Bundesliga, DFB-Pokal | OpenLigaDB | ESPN |
| Premier League | to be decided | ESPN |
| Champions League, Europa League, Conference League | ESPN | ESPN |

## Current state (0.2.0, phase 1)

Competitions with an OpenLigaDB source (Bundesliga; 2. Bundesliga, DFB-Pokal and Premier League
use the same code but are not verified yet) get one sensor per config entry:

- **Entity:** device `Matchday <competition>`, sensor `Matchday` / `Spieltag`
  (e.g. `sensor.matchday_bundesliga_spieltag` on a German system).
- **State:** the matchday name from OpenLigaDB, e.g. `4. Spieltag`.
- **Attributes:** `matches` (OpenLigaDB match objects enriched with ESPN data: `espn_id`,
  `status` pre/live/ht/post, `minute`, `live_score`, `scorers`, `cards`, `oldb_score`, kickoff
  corrected from ESPN), `matchday`, `season`, `competition`. `matches` is excluded from the
  recorder (too large).
- **Matchday choice:** by date — the last or the next match, whichever is closer in time, so
  the display switches in the middle of a break. A running match blocks the switch.
- **Polling:** 60 s while a match is live or starts within 10 minutes, 5 min around the
  matchday, 30 min otherwise. Errors keep the last good data.
- **Not yet:** substitutions, statistics, lineups and half-time score (ESPN summaries, phase 2);
  tables; UEFA competitions (setting one up fails with a clear error until phase 3).

`scripts/compare_shadow.py` compares the sensor with the template sensor it replaces
(`HA_TOKEN`, optional `HA_URL`).

## Development

```bash
uv venv --python 3.14 && uv pip install -r requirements-test.txt
.venv/bin/pytest -q
uvx ruff check custom_components tests scripts
```

Tests use recorded OpenLigaDB/ESPN answers from `tests/fixtures/`.

**Live recordings and replay.** Finished matchdays cannot show how live data evolves, so
`scripts/record_espn.py` records a competition day from ESPN (every 30 s, only changed
scoreboards and live match summaries, into `recordings/`, which is not versioned):

```bash
scripts/record_espn.py --slug uefa.nations --date 20260926 --until "2026-09-26 23:15"
```

`scripts/make_replay_fixture.py` cuts a slim, reproducible fixture out of it (selected games,
only snapshots in which they change; `--game`, `--summaries`). The committed
`tests/fixtures/replay/uefa.nations-20260926.json.gz` holds ESP @ ENG, CRO @ CZE (VAR) and
SCO @ SVN. `tests/test_replay.py` replays it against a synthetic OpenLigaDB matchday with a
simulated clock that advances in the coordinator's own `update_interval` (`tests/replay.py`).

## Installation

Not available through HACS yet.

## License

MIT

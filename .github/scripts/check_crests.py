"""Check the crest table: does every URL load, and does every team have an entry?

Usage: python3 .github/scripts/check_crests.py [season]

Loads every URL in ``custom_components/matchday/crests.py`` (slowly, Wikimedia
answers bursts with 429) and lists the teams of the leagues below that have no
entry, e.g. after promotion. The season defaults to the one running now (from
July on the new one). Exit code 1 if anything is wrong.
"""

from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

CRESTS_FILE = Path(__file__).parents[2] / "custom_components" / "matchday" / "crests.py"
LEAGUES = ("bl1", "bl2", "pl")
USER_AGENT = "ha-matchday-crest-check/1.0 (https://github.com/nuggie/ha-matchday)"
MAX_BYTES = 200_000


def load_crests() -> dict[int, str]:
    """Import crests.py on its own (the package itself needs Home Assistant)."""
    spec = importlib.util.spec_from_file_location("crests", CRESTS_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CRESTS


def fetch(url: str) -> tuple[int, str, bytes]:
    """Return status, content type and body; retry twice on 429."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for _ in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return (
                    response.status,
                    response.headers.get("Content-Type", ""),
                    response.read(),
                )
        except urllib.error.HTTPError as err:
            if err.code != 429:
                return err.code, "", b""
            time.sleep(10)
    return 429, "", b""


def main() -> int:
    """Run both checks and print what is wrong."""
    today = date.today()
    season = int(sys.argv[1]) if len(sys.argv) > 1 else today.year - (today.month < 7)
    crests = load_crests()
    problems = 0

    for team_id, url in crests.items():
        status, content_type, body = fetch(url)
        if status != 200 or not content_type.startswith("image/"):
            print(f"KAPUTT   {team_id}: HTTP {status} {content_type} {url}")
            problems += 1
        elif len(body) > MAX_BYTES:
            print(f"ZU GROSS {team_id}: {len(body) // 1024} KB {url}")
            problems += 1
        time.sleep(1.5)

    for league in LEAGUES:
        _status, _type, body = fetch(
            f"https://api.openligadb.de/getavailableteams/{league}/{season}"
        )
        for team in json.loads(body or b"[]"):
            if team.get("teamId") not in crests:
                print(
                    f"FEHLT    {league} {season}: {team.get('teamId')} {team.get('teamName')}"
                )
                problems += 1

    print(f"{len(crests)} Wappen geprueft, {problems} Probleme")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

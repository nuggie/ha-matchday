"""Constants for the Matchday integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Literal

DOMAIN = "matchday"

LOGGER = logging.getLogger(__package__)

CONF_COMPETITION = "competition"


@dataclass(frozen=True, slots=True)
class Competition:
    """A football competition Matchday can follow.

    ``openligadb_shortcut`` is set for competitions whose fixture list and
    matchday grouping come from OpenLigaDB. ESPN supplies live data for all of
    them; competitions without an OpenLigaDB shortcut run on ESPN alone.

    ``team_names`` selects how team names are normalized to pair OpenLigaDB
    matches with ESPN events: ``"de"`` for German clubs, ``"en"`` for English ones.

    ``has_table`` is False for cups: no table sensor, no standings requests.

    ``espn_qual_slug`` is the ESPN slug of the qualifying rounds of an ESPN-only
    competition; its events are part of the season next to those of ``espn_slug``.

    ``oldb_results`` is False where OpenLigaDB results are not maintained and must
    not be passed on: the private Premier League carries automatic 0:0
    placeholders once a match kicked off, so ``matchResults`` is emptied (as the
    Jinja block does); the score comes from ESPN only.
    """

    key: str
    name: str
    espn_slug: str
    openligadb_shortcut: str | None = None
    team_names: Literal["de", "en"] = "de"
    has_table: bool = True
    oldb_results: bool = True
    espn_qual_slug: str | None = None


COMPETITIONS: dict[str, Competition] = {
    competition.key: competition
    for competition in (
        Competition("bl1", "Bundesliga", "ger.1", "bl1"),
        Competition("bl2", "2. Bundesliga", "ger.2", "bl2"),
        Competition("dfb", "DFB-Pokal", "ger.dfb_pokal", "dfb", has_table=False),
        # OpenLigaDB has no official Premier League. "pl" is a privately maintained
        # league (id 5996) whose fixtures a sync job keeps in line with ESPN; its
        # results are not maintained. An ESPN-only mode is not part of phase 2.
        Competition(
            "pl",
            "Premier League",
            "eng.1",
            "pl",
            team_names="en",
            oldb_results=False,
        ),
        Competition(
            "ucl",
            "UEFA Champions League",
            "uefa.champions",
            espn_qual_slug="uefa.champions_qual",
        ),
        Competition(
            "uel",
            "UEFA Europa League",
            "uefa.europa",
            espn_qual_slug="uefa.europa_qual",
        ),
        Competition(
            "uecl",
            "UEFA Conference League",
            "uefa.europa.conf",
            espn_qual_slug="uefa.europa.conf_qual",
        ),
    )
}

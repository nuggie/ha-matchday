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
    """

    key: str
    name: str
    espn_slug: str
    openligadb_shortcut: str | None = None
    team_names: Literal["de", "en"] = "de"


COMPETITIONS: dict[str, Competition] = {
    competition.key: competition
    for competition in (
        Competition("bl1", "Bundesliga", "ger.1", "bl1"),
        Competition("bl2", "2. Bundesliga", "ger.2", "bl2"),
        Competition("dfb", "DFB-Pokal", "ger.dfb_pokal", "dfb"),
        # OpenLigaDB has no official Premier League. "pl" is a privately maintained
        # league (id 5996); whether Matchday keeps it or offers an ESPN-only mode is
        # still open (see project plan, decision 4).
        Competition("pl", "Premier League", "eng.1", "pl", team_names="en"),
        Competition("ucl", "UEFA Champions League", "uefa.champions"),
        Competition("uel", "UEFA Europa League", "uefa.europa"),
        Competition("uecl", "UEFA Conference League", "uefa.europa.conf"),
    )
}

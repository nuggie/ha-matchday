"""Constants for the Matchday integration."""

from __future__ import annotations

from dataclasses import dataclass

DOMAIN = "matchday"

CONF_COMPETITION = "competition"


@dataclass(frozen=True, slots=True)
class Competition:
    """A football competition Matchday can follow.

    ``openligadb_shortcut`` is set for competitions whose fixture list and
    matchday grouping come from OpenLigaDB. ESPN supplies live data for all of
    them; competitions without an OpenLigaDB shortcut run on ESPN alone.
    """

    key: str
    name: str
    espn_slug: str
    openligadb_shortcut: str | None = None


COMPETITIONS: dict[str, Competition] = {
    competition.key: competition
    for competition in (
        Competition("bl1", "Bundesliga", "ger.1", "bl1"),
        Competition("bl2", "2. Bundesliga", "ger.2", "bl2"),
        Competition("dfb", "DFB-Pokal", "ger.dfb_pokal", "dfb"),
        # OpenLigaDB has no official Premier League. "pl" is a privately maintained
        # league (id 5996); whether Matchday keeps it or offers an ESPN-only mode is
        # still open (see project plan, decision 4).
        Competition("pl", "Premier League", "eng.1", "pl"),
        Competition("ucl", "UEFA Champions League", "uefa.champions"),
        Competition("uel", "UEFA Europa League", "uefa.europa"),
        Competition("uecl", "UEFA Conference League", "uefa.europa.conf"),
    )
}

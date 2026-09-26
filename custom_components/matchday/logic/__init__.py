"""Pure functions behind Matchday: no Home Assistant state, no network."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_utc(value: object) -> datetime | None:
    """Parse an ISO timestamp from OpenLigaDB or ESPN into an aware UTC datetime.

    OpenLigaDB sends ``2026-09-18T18:30:00Z``, ESPN ``2026-09-18T18:30Z``.
    Naive values are taken as UTC. Returns ``None`` for anything unparsable.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

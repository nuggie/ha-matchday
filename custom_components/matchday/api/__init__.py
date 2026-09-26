"""HTTP clients for the data sources Matchday combines (OpenLigaDB, ESPN)."""

from __future__ import annotations

from typing import Any

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class MatchdayApiError(Exception):
    """A data source could not be reached or answered with something unusable."""


async def async_get_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, str] | None = None,
) -> Any:
    """GET ``url`` and decode the JSON body.

    Raises:
        MatchdayApiError: on network errors, timeouts, non-200 answers or bodies
            that are not JSON. Callers decide whether that is fatal.
    """
    try:
        async with session.get(url, params=params, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                raise MatchdayApiError(f"{url} answered HTTP {resp.status}")
            # OpenLigaDB occasionally sends JSON as text/plain; accept any type.
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise MatchdayApiError(f"{url} failed: {err!r}") from err
    except ValueError as err:  # JSON decode errors
        raise MatchdayApiError(f"{url} returned invalid JSON: {err}") from err

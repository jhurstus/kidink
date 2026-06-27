"""HTTP fetch for the family-calendar ICS feed.

The only calendar module that touches the network. The feed URL is secret and
unauthenticated, so it is passed as ``SecretStr`` and never appears in the raised
error message or any log line.
"""

import httpx
from pydantic import SecretStr


class CalendarFetchError(Exception):
    """Raised when the family-calendar feed can't be retrieved.

    The message is deliberately URL-free; map this to a 500 in the render route.
    """


def fetch_ics(url: SecretStr, *, timeout: float = 10.0) -> str:
    """GET the ICS feed at ``url`` and return its text (spec §6.1).

    Raises :class:`CalendarFetchError` on any transport/HTTP error.
    """
    try:
        response = httpx.get(
            url.get_secret_value(), timeout=timeout, follow_redirects=True
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # Never include the URL or the underlying exception text (may echo the URL).
        raise CalendarFetchError("family calendar fetch failed") from exc
    return response.text

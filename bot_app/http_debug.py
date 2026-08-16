"""Debug logging for Discord HTTP rate limits.

discord.py logs a single warning when a request is rate limited but does not
surface the response headers that explain the limit. This patches aiohttp's
request path once per process so every 429 response also logs those headers
at debug level.
"""

from __future__ import annotations

import logging

import aiohttp

LOGGER = logging.getLogger(__name__)

_RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset-After",
    "X-RateLimit-Bucket",
    "Retry-After",
)

_patched = False


def install_rate_limit_debug_logging() -> None:
    """Log rate-limit response headers whenever any HTTP request returns 429."""
    global _patched
    if _patched:
        return
    _patched = True

    original_request = aiohttp.ClientSession._request

    async def _request(self, method, url, **kwargs):
        response = await original_request(self, method, url, **kwargs)
        if response.status == 429:
            headers = {name: response.headers.get(name) for name in _RATE_LIMIT_HEADERS}
            LOGGER.debug("Rate limited on %s %s: %s", method, url, headers)
        return response

    aiohttp.ClientSession._request = _request

"""Rate-limit header debug logging."""

import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

import bot_app.http_debug as http_debug


class RateLimitDebugLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_request = aiohttp.ClientSession._request
        http_debug._patched = False

    def tearDown(self) -> None:
        aiohttp.ClientSession._request = self._original_request
        http_debug._patched = False

    async def test_logs_rate_limit_headers_on_429(self) -> None:
        """Verify that rate limit headers get logged when a request is 429'd."""
        response = Mock(
            status=429,
            headers={
                "X-RateLimit-Limit": "5",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": "1.5",
                "X-RateLimit-Bucket": "abc123",
                "Retry-After": "1.5",
            },
        )
        with patch.object(
            aiohttp.ClientSession, "_request", AsyncMock(return_value=response)
        ):
            http_debug.install_rate_limit_debug_logging()
            session = aiohttp.ClientSession.__new__(aiohttp.ClientSession)
            with self.assertLogs(http_debug.LOGGER, level="DEBUG") as captured:
                result = await aiohttp.ClientSession._request(session, "GET", "http://example.test")

        self.assertIs(result, response)
        message = captured.output[0]
        for header in (
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset-After",
            "X-RateLimit-Bucket",
            "Retry-After",
        ):
            self.assertIn(header, message)

    async def test_does_not_log_for_successful_responses(self) -> None:
        """Verify that successful responses are not logged."""
        response = Mock(status=200, headers={})
        with patch.object(
            aiohttp.ClientSession, "_request", AsyncMock(return_value=response)
        ):
            http_debug.install_rate_limit_debug_logging()
            session = aiohttp.ClientSession.__new__(aiohttp.ClientSession)
            with self.assertRaises(AssertionError):
                with self.assertLogs(http_debug.LOGGER, level="DEBUG"):
                    await aiohttp.ClientSession._request(session, "GET", "http://example.test")

    def test_installing_twice_only_patches_once(self) -> None:
        """Verify that installing twice only patches once."""
        http_debug.install_rate_limit_debug_logging()
        patched_once = aiohttp.ClientSession._request
        http_debug.install_rate_limit_debug_logging()
        self.assertIs(aiohttp.ClientSession._request, patched_once)


if __name__ == "__main__":
    unittest.main()

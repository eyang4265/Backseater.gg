"""Riot client cache policy."""

import unittest
from unittest.mock import Mock, patch

import requests

from bot_app.riot import RateLimiter, RiotAPIError, RiotClient, TTLCache


class TTLCacheTests(unittest.TestCase):
    def test_none_is_not_cached(self) -> None:
        """Verify that none is not cached."""
        calls = 0

        def produce():
            """Produce the value used by this test."""
            nonlocal calls
            calls += 1
            return None

        cache = TTLCache(60)
        cache.get_or_set("key", produce)
        cache.get_or_set("key", produce)
        self.assertEqual(calls, 2)


class RateLimiterTests(unittest.TestCase):
    def test_sliding_window_waits_until_budget_expires(self) -> None:
        """Verify that sliding window waits until budget expires."""
        now = 0.0

        def monotonic():
            """Return the current simulated monotonic time."""
            return now

        def sleep(seconds):
            """Advance the simulated clock."""
            nonlocal now
            now += seconds

        limiter = RateLimiter(((2, 10.0),))
        with (
            patch("bot_app.riot.time.monotonic", side_effect=monotonic),
            patch("bot_app.riot.time.sleep", side_effect=sleep),
        ):
            limiter.acquire()
            limiter.acquire()
            limiter.acquire()
        self.assertGreaterEqual(now, 10.0)

    def test_retry_budget_exhaustion_raises(self) -> None:
        """Verify that retry budget exhaustion raises."""
        client = object.__new__(RiotClient)
        client._max_retries = 2
        client._timeout = 1
        client._limiter = Mock()
        client._session = Mock()
        client._session.get.side_effect = requests.ConnectionError("offline")
        with patch("bot_app.riot.time.sleep"), self.assertRaises(RiotAPIError):
            client._get("americas", "/test")
        self.assertEqual(client._session.get.call_count, 2)

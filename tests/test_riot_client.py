"""Riot client cache policy."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
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

    def test_concurrent_misses_are_coalesced(self) -> None:
        """Only one producer runs while other callers wait for the same key."""
        calls = 0
        calls_lock = Lock()
        started = Event()
        release = Event()

        def produce():
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            release.wait(timeout=2)
            return "value"

        cache = TTLCache(60)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(cache.get_or_set, "key", produce) for _ in range(4)]
            self.assertTrue(started.wait(timeout=2))
            release.set()
            self.assertEqual([future.result() for future in futures], ["value"] * 4)
        self.assertEqual(calls, 1)


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

    def test_match_timeline_is_cached_after_the_first_fetch(self) -> None:
        """Repeated chart/rating views do not re-fetch an immutable timeline."""
        client = object.__new__(RiotClient)
        client._timeline_cache = TTLCache(60)
        client._match_cache = Mock()
        client._match_cache.get_timeline.return_value = None
        client._get = Mock(return_value={"info": {"frames": []}})

        first = client.match_timeline("NA1_1", "NA1")
        second = client.match_timeline("NA1_1", "NA1")

        self.assertEqual(first, second)
        client._get.assert_called_once()
        client._match_cache.put_timeline.assert_called_once_with("NA1_1", first)

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

"""Riot match endpoint integration with the injected cache."""

import unittest
from unittest.mock import Mock

from bot_app.riot import RiotClient


class RiotMatchCacheTests(unittest.TestCase):
    def _client(self, cache):
        """Handle client."""
        client = object.__new__(RiotClient)
        client._match_cache = cache
        client._get = Mock(return_value={"info": {"gameEndTimestamp": 1}})
        return client

    def test_cache_hit_skips_http(self) -> None:
        """Verify that cache hit skips http."""
        cache = Mock()
        cache.get.return_value = {"cached": True}
        client = self._client(cache)
        self.assertEqual(client.match("NA1_1", "NA1"), {"cached": True})
        client._get.assert_not_called()

    def test_cache_miss_fetches_and_stores(self) -> None:
        """Verify that cache miss fetches and stores."""
        cache = Mock()
        cache.get.return_value = None
        client = self._client(cache)
        payload = client.match("NA1_1", "NA1")
        client._get.assert_called_once()
        cache.put.assert_called_once_with("NA1_1", "NA1", payload)

    def test_disabled_cache_bypasses_cache_operations(self) -> None:
        """Verify that disabled cache bypasses cache operations."""
        client = self._client(None)
        self.assertEqual(
            client.match("NA1_1", "NA1"), {"info": {"gameEndTimestamp": 1}}
        )
        client._get.assert_called_once()

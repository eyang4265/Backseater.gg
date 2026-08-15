"""Concurrent per-guild route updates are atomic."""

import threading
import unittest

from bot_app.commands.guilds import set_guild_channel, unset_guild_channel
from bot_app.store import load_guild_channels
from tests.support import temporary_state


class GuildStoreTests(unittest.TestCase):
    def test_concurrent_setchannel_calls_keep_both_routes(self) -> None:
        """Verify that concurrent setchannel calls keep both routes."""
        with temporary_state():
            barrier = threading.Barrier(2)

            def set_route(guild_id, channel_id):
                """Set route."""
                barrier.wait()
                set_guild_channel(guild_id, channel_id)

            threads = [
                threading.Thread(target=set_route, args=("1", 10)),
                threading.Thread(target=set_route, args=("2", 20)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(load_guild_channels(), {"1": 10, "2": 20})
            self.assertTrue(unset_guild_channel("1"))
            self.assertFalse(unset_guild_channel("missing"))

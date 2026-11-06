"""Worlds overrides replace general routes and require channel permissions."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot_app import worlds_routing as routing
from bot_app.commands.setworldschannel import WorldsChannelCommands


class WorldsRouteStoreTests(unittest.TestCase):
    def test_concurrent_routes_are_retained_and_removal_is_scoped(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(routing, "STATE_PATH", Path(folder) / "routes.json"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda args: routing.set_worlds_channel(*args), [(1, 10), (2, 20)]))
            self.assertEqual(routing.load_worlds_channels(), {"1": 10, "2": 20})
            routing.remove_worlds_channel(1)
            self.assertEqual(routing.load_worlds_channels(), {"2": 20})


class WorldsRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_override_replaces_regular_and_fallback_channels_for_its_server(self):
        bot = SimpleNamespace(get_channel=lambda _: SimpleNamespace(guild=SimpleNamespace(id=1)))
        with patch.object(routing, "load_guild_channels", return_value={"1": 10, "2": 20}), \
             patch.object(routing, "load_worlds_channels", return_value={"1": 30}), \
             patch.object(routing, "get_settings", return_value=SimpleNamespace(announcement_channel_id=10)):
            self.assertEqual(await routing.resolve_worlds_channels(bot), {20, 30})

    async def test_no_overrides_preserves_existing_default_routes(self):
        with patch.object(routing, "load_guild_channels", return_value={"1": 10}), \
             patch.object(routing, "load_worlds_channels", return_value={}), \
             patch.object(routing, "get_settings", return_value=SimpleNamespace(announcement_channel_id=20)):
            self.assertEqual(await routing.resolve_worlds_channels(Mock()), {10, 20})

    async def test_uncached_fallback_is_resolved_before_applying_override(self):
        bot = SimpleNamespace(get_channel=lambda _: None,
                              fetch_channel=AsyncMock(return_value=SimpleNamespace(guild=SimpleNamespace(id=1))))
        with patch.object(routing, "load_guild_channels", return_value={}), \
             patch.object(routing, "load_worlds_channels", return_value={"1": 30}), \
             patch.object(routing, "get_settings", return_value=SimpleNamespace(announcement_channel_id=10)):
            self.assertEqual(await routing.resolve_worlds_channels(bot), {30})


class WorldsChannelCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        preferences = patch("bot_app.channel_preferences.STATE_PATH", Path(self.temp.name) / "preferences.json")
        preferences.start()
        self.addCleanup(preferences.stop)

    def context(self, *, allowed=True):
        perms = SimpleNamespace(**{name: allowed for name in (
            "view_channel", "send_messages", "embed_links", "add_reactions", "read_message_history")})
        channel = SimpleNamespace(id=30, mention="<#30>", permissions_for=lambda _: perms)
        return SimpleNamespace(guild=SimpleNamespace(id=1, me=Mock()), channel=channel,
                               defer=AsyncMock(), respond=AsyncMock())

    async def test_defaults_to_current_channel_and_defers_before_saving(self):
        ctx = self.context()

        def save(guild, channel, kind):
            ctx.defer.assert_awaited_once_with(ephemeral=True)
            self.assertEqual((guild, channel, kind), (1, 30, "worlds"))

        with patch("bot_app.commands.setworldschannel.log_command"), \
             patch("bot_app.commands.setworldschannel.configure_channel", side_effect=save) as store:
            await WorldsChannelCommands.setworldschannel.callback(WorldsChannelCommands(Mock()), ctx)
        store.assert_called_once()
        self.assertIn("<#30>", ctx.respond.call_args.kwargs["embed"].description)

    async def test_missing_permissions_does_not_save_route(self):
        ctx = self.context(allowed=False)
        with patch("bot_app.commands.setworldschannel.log_command"), \
             patch("bot_app.commands.setworldschannel.configure_channel") as save:
            await WorldsChannelCommands.setworldschannel.callback(WorldsChannelCommands(Mock()), ctx)
        save.assert_not_called()
        self.assertIn("Add Reactions", ctx.respond.call_args.kwargs["embed"].description)

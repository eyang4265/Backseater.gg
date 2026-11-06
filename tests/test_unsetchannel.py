"""Opt-outs must beat saved and fallback channels for every automatic post family."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot_app import daily_summary, worlds_routing
from bot_app.announce import resolve_announcement_channels
from bot_app.channel_settings import configure_channel, disable_channels
from bot_app.channel_preferences import disable_server, disabled_guilds
from bot_app.commands.setchannel import SetChannelCommands
from bot_app.commands.setworldschannel import WorldsChannelCommands
from bot_app.commands.unsetchannel import UnsetChannelCommands
from bot_app.guild_routing import set_guild_channel
from bot_app.store import Account, load_guild_channels
from tests.support import temporary_state


class UnsetChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_unset_clears_both_routes_and_each_set_command_reenables_only_its_family(self):
        with temporary_state():
            set_guild_channel(1, 10)
            worlds_routing.set_worlds_channel(1, 30)
            permissions = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True,
                                          add_reactions=True, read_message_history=True)
            channel = SimpleNamespace(id=40, mention="<#40>", permissions_for=lambda _: permissions)
            ctx = SimpleNamespace(guild=SimpleNamespace(id=1, me=Mock()), channel=channel,
                                  defer=AsyncMock(), respond=AsyncMock())
            with patch("bot_app.commands.unsetchannel.log_command"):
                await UnsetChannelCommands.unsetchannel.callback(UnsetChannelCommands(), ctx)
            ctx.defer.assert_awaited_once_with(ephemeral=True)
            self.assertEqual(load_guild_channels(), {})
            self.assertEqual(worlds_routing.load_worlds_channels(), {})
            self.assertEqual(disabled_guilds("matches"), {"1"})
            self.assertEqual(disabled_guilds("worlds"), {"1"})
            with patch("bot_app.commands.setchannel.log_command"):
                await SetChannelCommands.setchannel.callback(SetChannelCommands(), ctx)
            self.assertEqual(disabled_guilds("matches"), set())
            self.assertEqual(disabled_guilds("worlds"), {"1"})
            with patch("bot_app.commands.setworldschannel.log_command"):
                await WorldsChannelCommands.setworldschannel.callback(WorldsChannelCommands(Mock()), ctx)
            self.assertEqual(disabled_guilds("worlds"), set())

    async def test_disabled_server_has_no_worlds_fallback_or_override(self):
        with temporary_state():
            disable_server(1)
            bot = SimpleNamespace(get_channel=lambda _: SimpleNamespace(guild=SimpleNamespace(id=1)))
            with patch.object(worlds_routing, "load_guild_channels", return_value={"1": 10, "2": 20}), \
                 patch.object(worlds_routing, "load_worlds_channels", return_value={"1": 30}), \
                 patch.object(worlds_routing, "get_settings", return_value=SimpleNamespace(announcement_channel_id=10)):
                self.assertEqual(await worlds_routing.resolve_worlds_channels(bot), {20})

    async def test_disabled_player_routes_do_not_disable_other_servers(self):
        with temporary_state():
            disable_server(1)
            fallback = SimpleNamespace(id=10, guild=SimpleNamespace(id=1))
            other = SimpleNamespace(id=20, guild=SimpleNamespace(id=2))
            bot = SimpleNamespace(get_guild=lambda _: SimpleNamespace(get_member=lambda _: object()),
                                  get_channel=lambda channel_id: other)
            channels = await resolve_announcement_channels(
                bot, {"player"}, configured={"1": 10, "2": 20},
                accounts={"u": Account("1", "player", "NA1", "Name#NA1")}, global_channel=fallback)
            self.assertEqual(channels, [other])

    async def test_daily_recap_does_not_send_to_disabled_default_channel(self):
        with temporary_state():
            disable_server(1)
            now = datetime(2026, 10, 28, 20, tzinfo=timezone.utc)
            start, end = daily_summary.completed_window(now)
            channel = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
            bot = SimpleNamespace(get_channel=lambda _: channel)
            with patch.object(daily_summary, "load_guild_channels", return_value={}), \
                 patch.object(daily_summary, "get_settings", return_value=SimpleNamespace(announcement_channel_id=10)), \
                 patch.object(daily_summary, "_load_due", return_value=[(10, [], {}, start, end)]):
                await daily_summary.poll_daily_summary(bot, now=now)
            channel.send.assert_not_awaited()


class ChannelCategoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_and_unset_categories_leave_the_other_route_enabled(self):
        with temporary_state():
            set_guild_channel(1, 10)
            worlds_routing.set_worlds_channel(1, 30)
            configure_channel(1, 40, "worlds")
            self.assertEqual(load_guild_channels(), {"1": 10})
            self.assertEqual(worlds_routing.load_worlds_channels(), {"1": 40})
            configure_channel(1, 50, "players")
            self.assertEqual(load_guild_channels(), {"1": 50})
            self.assertEqual(worlds_routing.load_worlds_channels(), {"1": 40})
            disable_channels(1, "worlds")
            self.assertEqual(load_guild_channels(), {"1": 50})
            self.assertEqual(disabled_guilds("matches"), set())
            self.assertEqual(disabled_guilds("worlds"), {"1"})
            configure_channel(1, 60, "both")
            self.assertEqual(load_guild_channels(), {"1": 60})
            self.assertEqual(worlds_routing.load_worlds_channels(), {"1": 60})
            disable_channels(1, "players")
            self.assertEqual(disabled_guilds("matches"), {"1"})
            self.assertEqual(disabled_guilds("worlds"), set())
            self.assertEqual(worlds_routing.load_worlds_channels(), {"1": 60})

    async def test_player_only_change_preserves_an_inherited_worlds_destination(self):
        with temporary_state():
            set_guild_channel(1, 10)
            configure_channel(1, 20, "players")
            self.assertEqual(load_guild_channels(), {"1": 20})
            self.assertEqual(worlds_routing.load_worlds_channels(), {"1": 10})
            disable_channels(1, "players")
            self.assertEqual(worlds_routing.load_worlds_channels(), {"1": 10})
            self.assertEqual(disabled_guilds("worlds"), set())

    async def test_command_worlds_type_defaults_to_the_invoking_channel(self):
        permissions = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True,
                                      add_reactions=True, read_message_history=True)
        channel = SimpleNamespace(id=40, mention="<#40>", permissions_for=lambda _: permissions)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1, me=Mock()), channel=channel,
                              defer=AsyncMock(), respond=AsyncMock())
        with patch("bot_app.commands.setchannel.log_command"), \
             patch("bot_app.commands.setchannel.configure_channel") as save:
            await SetChannelCommands.setchannel.callback(SetChannelCommands(), ctx, type="worlds")
        save.assert_called_once_with(1, 40, "worlds")
        ctx.defer.assert_awaited_once_with(ephemeral=True)
        with patch("bot_app.commands.unsetchannel.log_command"), \
             patch("bot_app.commands.unsetchannel.disable_channels") as disable:
            await UnsetChannelCommands.unsetchannel.callback(UnsetChannelCommands(), ctx, type="players")
        disable.assert_called_once_with(1, "players")

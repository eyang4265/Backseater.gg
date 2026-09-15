"""Membership-based announcement routing."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.announce import resolve_announcement_channels
from bot_app.store import Account


class GuildRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_to_guild_containing_highlighted_player(self) -> None:
        """Verify that routes to guild containing highlighted player."""
        channel = Mock(id=10)
        guild = Mock()
        guild.get_member.side_effect = lambda discord_id: (
            object() if discord_id == 1 else None
        )
        bot = Mock()
        bot.get_guild.return_value = guild
        bot.get_channel.return_value = channel
        with (
            patch("bot_app.announce.load_guild_channels", return_value={"5": 10}),
            patch(
                "bot_app.announce.load_accounts",
                return_value={"1": Account("1", "p1", "NA1", "One#NA1")},
            ),
        ):
            channels = await resolve_announcement_channels(
                bot, {"p1"}, global_channel=None
            )
        self.assertEqual(channels, [channel])

    async def test_legacy_channel_is_fallback_without_guild_config(self) -> None:
        """Verify that legacy channel is fallback without guild config."""
        channel = Mock()
        with (
            patch("bot_app.announce.load_guild_channels", return_value={}),
            patch(
                "bot_app.announce.resolve_announcement_channel", return_value=channel
            ),
        ):
            self.assertEqual(
                await resolve_announcement_channels(Mock(), {"p1"}), [channel]
            )

    async def test_legacy_channel_remains_when_configured_guild_does_not_match(
        self,
    ) -> None:
        """Verify that legacy channel remains when configured guild does not match."""
        global_channel = Mock(id=99)
        guild = Mock()
        bot = Mock()
        bot.get_guild.return_value = guild
        with patch(
            "bot_app.announce.resolve_announcement_channel", return_value=global_channel
        ):
            channels = await resolve_announcement_channels(
                bot,
                {"different-puuid"},
                configured={"5": 10},
                accounts={"1": Account("1", "p1", "NA1", "One#NA1")},
            )
        self.assertEqual(channels, [global_channel])

    async def test_transient_member_failure_routes_conservatively(self) -> None:
        """Verify that transient member failure routes conservatively."""
        global_channel = Mock(id=99)
        guild_channel = Mock(id=10)
        response = Mock(status=500, reason="server error")
        guild = Mock()
        guild.get_member.return_value = None
        guild.fetch_member = AsyncMock(
            side_effect=discord.HTTPException(response, "temporary failure")
        )
        bot = Mock()
        bot.get_guild.return_value = guild
        bot.get_channel.return_value = guild_channel
        with self.assertLogs("bot_app.announce", level="WARNING"):
            channels = await resolve_announcement_channels(
                bot,
                {"p1"},
                configured={"5": 10},
                accounts={"1": Account("1", "p1", "NA1", "One#NA1")},
                global_channel=global_channel,
            )
        self.assertEqual(channels, [global_channel, guild_channel])

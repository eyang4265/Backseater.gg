"""Announcement fan-out renders once and creates one-use attachments."""

import io
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.announce import MatchAnnouncement, publish


class PublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_embed_is_built_once_and_files_are_distinct(self) -> None:
        """Verify that embed is built once and files are distinct."""
        channels = [Mock(id=1, send=AsyncMock()), Mock(id=2, send=AsyncMock())]
        announcement = MatchAnnouncement("text", "Victory", {"info": {}}, {"p1"})
        chart = discord.File(io.BytesIO(b"png"), filename="damage.png")
        build = AsyncMock(return_value=(discord.Embed(title="match"), chart))
        with (
            patch("bot_app.announce.load_guild_channels", return_value={}),
            patch("bot_app.announce.load_accounts", return_value={}),
            patch("bot_app.announce.resolve_announcement_channel", return_value=None),
            patch(
                "bot_app.announce.resolve_announcement_channels", return_value=channels
            ),
            patch("bot_app.announce.build_announcement_embed", build),
        ):
            await publish(Mock(), [announcement])

        build.assert_awaited_once_with(announcement)
        files = [channel.send.await_args.kwargs["file"] for channel in channels]
        self.assertIsNot(files[0], files[1])
        self.assertEqual(files[0].filename, files[1].filename)

    async def test_zero_channels_is_warned_and_not_rendered(self) -> None:
        """Verify that zero channels is warned and not rendered."""
        build = AsyncMock()
        with (
            patch("bot_app.announce.load_guild_channels", return_value={}),
            patch("bot_app.announce.load_accounts", return_value={}),
            patch("bot_app.announce.resolve_announcement_channel", return_value=None),
            patch("bot_app.announce.resolve_announcement_channels", return_value=[]),
            patch("bot_app.announce.build_announcement_embed", build),
            self.assertLogs("bot_app.announce", level="WARNING") as logs,
        ):
            await publish(
                Mock(), [MatchAnnouncement("text", "Victory", {"info": {}}, {"p1"})]
            )
        build.assert_not_awaited()
        self.assertTrue(any("zero channels" in message for message in logs.output))

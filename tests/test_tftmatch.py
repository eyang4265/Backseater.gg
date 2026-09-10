"""The ``/tftmatch`` command's response wiring."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.commands.shared import Target
from bot_app.commands.tftmatch import TftMatchCommands


class TftMatchResponseTests(unittest.IsolatedAsyncioTestCase):
    async def _invoke(self):
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=1),
            defer=AsyncMock(),
            respond=AsyncMock(return_value=None),
        )
        client = Mock()
        client.tft_match_ids.return_value = ["NA1_1"]
        client.tft_match.return_value = {"info": {"participants": [{"puuid": "me"}]}}
        cog = TftMatchCommands(Mock())
        with (
            patch(
                "bot_app.commands.tftmatch.tft_target_for",
                AsyncMock(return_value=Target("me", "NA1", "Me#NA1")),
            ),
            patch("bot_app.commands.tftmatch.get_client", return_value=client),
            patch(
                "bot_app.commands.tftmatch.format_match",
                return_value=SimpleNamespace(game_type="tft"),
            ),
            patch(
                "bot_app.commands.tftmatch.build_announcement_embed",
                AsyncMock(return_value=(discord.Embed(), None)),
            ),
            patch("bot_app.commands.tftmatch.MatchAnnouncementView"),
            patch("bot_app.commands.tftmatch.remember_match_view_state", AsyncMock()),
        ):
            await cog.tftmatch.callback(cog, ctx, None, None, None)
        return ctx

    async def test_no_chart_means_no_file_kwarg(self) -> None:
        """A chartless TFT announcement must not pass file=None to followup.send."""
        ctx = await self._invoke()
        ctx.respond.assert_awaited_once()
        self.assertNotIn("file", ctx.respond.await_args.kwargs)
        self.assertIsNone(ctx.respond.await_args.kwargs.get("file"))
        self.assertIn("embed", ctx.respond.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()

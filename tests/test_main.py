"""Top-level Discord event behavior."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from discord.ext import commands

from main import (
    ConnectState,
    handle_application_command_error,
    mention_reply_content,
    sync_and_restore_views,
)


class CommandErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_permissions_gets_an_ephemeral_response(self) -> None:
        """Verify that missing permissions gets an ephemeral response."""
        ctx = Mock(respond=AsyncMock())
        handled = await handle_application_command_error(
            ctx, commands.MissingPermissions(["manage_guild"])
        )
        self.assertTrue(handled)
        self.assertTrue(ctx.respond.await_args.kwargs["ephemeral"])

    async def test_unexpected_error_is_left_for_logging(self) -> None:
        """Verify that unexpected error is left for logging."""
        self.assertFalse(
            await handle_application_command_error(Mock(), RuntimeError("boom"))
        )


class SyncAndRestoreViewsTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_command_sync_does_not_block_view_restoration(self) -> None:
        """A bad option definition or Discord hiccup in sync_commands must
        not leave every existing embed's buttons dead after a restart."""
        bot = Mock(auto_sync_commands=True, sync_commands=AsyncMock(side_effect=RuntimeError("400 Bad Request")))
        settings = Mock(sync_commands_enabled=True)
        state = ConnectState()
        with patch("main.register_persistent_embed_views", return_value=2) as embed_views, patch(
            "main.register_persistent_command_views", return_value=3
        ) as command_views:
            await sync_and_restore_views(bot, settings, state)
        bot.sync_commands.assert_awaited_once()
        embed_views.assert_called_once_with(bot)
        command_views.assert_called_once_with(bot)
        self.assertTrue(state.persistent_views_restored)

    async def test_view_restoration_runs_only_once_per_process(self) -> None:
        """Verify that a second connect (reconnect) does not re-restore views."""
        bot = Mock(auto_sync_commands=False)
        settings = Mock(sync_commands_enabled=True)
        state = ConnectState(persistent_views_restored=True)
        with patch("main.register_persistent_embed_views") as embed_views, patch(
            "main.register_persistent_command_views"
        ) as command_views:
            await sync_and_restore_views(bot, settings, state)
        embed_views.assert_not_called()
        command_views.assert_not_called()

    async def test_a_broken_persisted_state_does_not_raise(self) -> None:
        """Verify that an unexpected restoration failure is swallowed and logged."""
        bot = Mock(auto_sync_commands=False)
        settings = Mock(sync_commands_enabled=True)
        state = ConnectState()
        with patch("main.register_persistent_embed_views", side_effect=RuntimeError("boom")):
            await sync_and_restore_views(bot, settings, state)
        self.assertFalse(state.persistent_views_restored)


class MentionReplyTests(unittest.TestCase):
    def test_mention_reply_includes_latency_and_command_directory(self) -> None:
        """Verify that mention replies include latency and command guidance."""
        self.assertEqual(
            mention_reply_content(0.123),
            "🏓 Pong! **123 ms**\nDo `/commands` for the list of commands.",
        )

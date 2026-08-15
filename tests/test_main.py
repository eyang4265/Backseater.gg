"""Top-level Discord event behavior."""

import unittest
from unittest.mock import AsyncMock, Mock

from discord.ext import commands

from main import handle_application_command_error


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

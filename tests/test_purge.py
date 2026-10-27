"""Owner-only bot-message purge command."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from bot_app.commands import register_all
from bot_app.commands.command_directory import is_owner_only, public_commands
from bot_app.commands.purge import PurgeCommands


class _HistoryChannel:
    def __init__(self, messages):
        self.id = 55
        self._messages = messages

    async def history(self, **kwargs):
        self.history_kwargs = kwargs
        for message in self._messages:
            yield message


class PurgeTests(unittest.TestCase):
    def test_registration_is_owner_only_with_required_integer(self) -> None:
        """The destructive command stays out of every public directory."""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            bot = discord.Bot()
            register_all(bot)
            command = next(
                item
                for item in bot.pending_application_commands
                if item.qualified_name == "purge"
            )
            self.assertTrue(is_owner_only(command))
            self.assertNotIn(command, public_commands(bot, tft=False))
            self.assertEqual([option.name for option in command.options], ["number"])
            option = command.options[0]
            self.assertTrue(option.required)
            self.assertEqual(option.input_type, discord.SlashCommandOptionType.integer)
            self.assertEqual((option.min_value, option.max_value), (1, 1000))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_deletes_only_latest_requested_bot_messages(self) -> None:
        """Other authors are skipped and scanning stops at the requested count."""

        async def exercise() -> None:
            bot_id = 10
            messages = [
                Mock(author=Mock(id=bot_id), delete=AsyncMock()),
                Mock(author=Mock(id=20), delete=AsyncMock()),
                Mock(author=Mock(id=bot_id), delete=AsyncMock()),
                Mock(author=Mock(id=bot_id), delete=AsyncMock()),
            ]
            channel = _HistoryChannel(messages)
            ctx = Mock(channel=channel, author=Mock(id=99), guild=Mock(id=1))
            ctx.defer = AsyncMock()
            ctx.edit = AsyncMock()
            cog = PurgeCommands(Mock(user=Mock(id=bot_id)))

            await PurgeCommands.purge.callback(cog, ctx, 2)

            ctx.defer.assert_awaited_once_with(ephemeral=True)
            messages[0].delete.assert_awaited_once()
            messages[1].delete.assert_not_awaited()
            messages[2].delete.assert_awaited_once()
            messages[3].delete.assert_not_awaited()
            self.assertEqual(
                channel.history_kwargs, {"limit": None, "oldest_first": False}
            )
            ctx.edit.assert_awaited_once_with(
                content="Deleted the latest 2 bot message(s)."
            )

        asyncio.run(exercise())

    def test_reports_when_fewer_bot_messages_exist(self) -> None:
        """The owner gets an exact partial count after history is exhausted."""

        async def exercise() -> None:
            message = Mock(author=Mock(id=10), delete=AsyncMock())
            ctx = Mock(
                channel=_HistoryChannel([message]),
                author=Mock(id=99),
                guild=Mock(id=1),
            )
            ctx.defer = AsyncMock()
            ctx.edit = AsyncMock()
            cog = PurgeCommands(Mock(user=Mock(id=10)))

            await PurgeCommands.purge.callback(cog, ctx, 3)

            ctx.edit.assert_awaited_once_with(
                content="Deleted 1 bot message(s); only 1 were found."
            )

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()

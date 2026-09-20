"""Public report controls can be used by someone other than the invoker."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.champstats import ChampionStatsReport
from bot_app.commands.champstats import ChampStatsView
from bot_app.commands.counterstats import CounterStatsView
from bot_app.paginator import Paginator


def _interaction():
    interaction = Mock()
    interaction.user.id = 2
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


class PublicButtonAccessTests(unittest.TestCase):
    def test_shared_paginator_accepts_other_reader(self):
        async def run():
            view = Paginator(
                ["one", "two"], author_id=1,
                render_page=lambda rows, page, pages: discord.Embed(description=rows[0]),
                page_size=1,
            )
            interaction = _interaction()
            self.assertTrue(await view.interaction_check(interaction))
            await view._go_to(1, interaction)
            interaction.response.edit_message.assert_awaited_once()

        asyncio.run(run())

    def test_champstats_accepts_other_reader(self):
        async def run():
            report = ChampionStatsReport("Garen", "Ranked", 1, 1, (), (), (), ())
            view = ChampStatsView(report, "player", author_id=1)
            interaction = _interaction()
            with patch.object(view, "_remember", new_callable=AsyncMock):
                await view._go_next(interaction)
            interaction.response.defer.assert_awaited_once()
            interaction.edit_original_response.assert_awaited_once()

        asyncio.run(run())

    def test_counterstats_role_and_page_accept_other_reader(self):
        async def run():
            view = CounterStatsView([], "me", "Garen", "All Games", "Top", False, "player", 1)
            view.add_role_buttons()
            interaction = _interaction()
            with patch.object(view, "_remember", new_callable=AsyncMock):
                await view._select_role("Jungle")(interaction)
                await view._go_next(interaction)
            self.assertEqual(interaction.response.defer.await_count, 2)
            self.assertEqual(interaction.edit_original_response.await_count, 2)
            self.assertEqual(view.enemy_role, "Jungle")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

"""Startup restoration for persistent public-command controls."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch

from bot_app.champstats import ChampionStatsReport, ChoiceRecord
from bot_app.command_views import register_persistent_command_views
from bot_app.commands.champstats import ChampStatsView
from bot_app.commands.counterstats import CounterStatsView


class PersistentStatsViewTests(unittest.TestCase):
    def test_champion_and_counter_stats_views_restore_after_restart(self) -> None:
        report = ChampionStatsReport(
            "Garen",
            "Ranked",
            3,
            2,
            (ChoiceRecord(1, "Conqueror", 3, 2),),
            (),
            (),
            (),
        )
        states = [
            {
                "message_id": 10,
                "kind": "champstats",
                "payload": {
                    "report": asdict(report),
                    "player_name": "Player#NA1",
                    "author_id": 1,
                    "apply_filter": True,
                    "selected": "Runes",
                    "page": 0,
                },
            },
            {
                "message_id": 11,
                "kind": "counterstats",
                "payload": {
                    "puuid": "player",
                    "server": "NA1",
                    "champion": "Garen",
                    "queue_scope": "Ranked",
                    "player_role": "Top",
                    "usage_filter": False,
                    "player_name": "Player#NA1",
                    "author_id": 1,
                    "laning": False,
                    "enemy_role": "Jungle",
                    "page": 0,
                },
            },
        ]

        async def restore():
            bot = Mock()
            with patch("bot_app.command_views.load_embed_button_states", return_value=states):
                count = register_persistent_command_views(bot)
            return bot, count

        bot, count = asyncio.run(restore())
        self.assertEqual(count, 2)
        first, second = [call.args[0] for call in bot.add_view.call_args_list]
        self.assertIsInstance(first, ChampStatsView)
        self.assertEqual(first.report, report)
        self.assertIsInstance(second, CounterStatsView)
        self.assertFalse(second._loaded)
        self.assertEqual(second.enemy_role, "Jungle")

    def test_restored_counter_view_loads_only_local_cached_data(self) -> None:
        async def exercise() -> CounterStatsView:
            view = CounterStatsView(
                None,
                "player",
                "Garen",
                "Ranked",
                "Top",
                False,
                "Player#NA1",
                1,
                server="NA1",
            )
            with patch(
                "bot_app.commands.counterstats._load_payloads", return_value=[{"info": {}}]
            ) as load:
                await view._ensure_loaded()
            load.assert_called_once_with("player", "NA1", scan_riot=False)
            return view

        view = asyncio.run(exercise())
        self.assertTrue(view._loaded)
        self.assertEqual(view.payloads, [{"info": {}}])


if __name__ == "__main__":
    unittest.main()

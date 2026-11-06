"""Worlds /stats command uses the shared acknowledged game browser."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.commands import register_all
from bot_app.commands.stats import StatsCommands
from bot_app.commands.worlds import WorldsCommands
from bot_app.worlds_views import schedule_tabs
from tests.test_worlds import event, parse


class StatsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_worlds_keeps_date_results_without_stats_picker(self):
        now = datetime(2026, 10, 28, 19, tzinfo=timezone.utc)
        series = parse(event())
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())
        with patch("bot_app.commands.worlds.log_command"), \
             patch("bot_app.worlds_command.fetch_series", return_value=series), \
             patch("bot_app.worlds_command.schedule_tabs", return_value=schedule_tabs(series, now=now)):
            await WorldsCommands.worlds.callback(WorldsCommands(Mock()), ctx)
        view = ctx.respond.call_args.kwargs["view"]
        self.assertFalse(view.show_stats)
        self.assertIsNone(view.series_select)
        self.assertEqual([b.label for b in view.day_buttons], ["Past Results", "Today"])

    async def test_stats_opens_latest_game_with_matchup_dropdown_only(self):
        from tests.test_worlds_stats import fixture
        from bot_app.worlds_stats import parse_final_stats
        series, game, window, details = fixture()
        series["state"] = "inProgress"
        older = dict(series, id="old", start=series["start"] - 86400, names=["OLD", "TEAM"])
        upcoming = dict(series, id="future", start=series["start"] + 86400, score=[0, 0])
        data = parse_final_stats(window, details, series, game)
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock(), channel=SimpleNamespace(id=10))

        def load(*args):
            ctx.defer.assert_awaited_once()
            return data

        with patch("bot_app.commands.stats.log_command"), \
             patch("bot_app.worlds_command.fetch_stats_series", return_value=[older, upcoming, series]), \
             patch("bot_app.worlds_stats_views.read_json", return_value={}), \
             patch("bot_app.worlds_stats.load_saved_stats", side_effect=load), \
             patch("bot_app.worlds_game_details.load_game_details", return_value={"bans": {}}), \
             patch("bot_app.charts.build_worlds_damage_share_chart", return_value=None):
            await StatsCommands.stats.callback(StatsCommands(Mock()), ctx)
        options = ctx.respond.call_args.kwargs
        view = options["view"]
        self.assertEqual(view.series["id"], series["id"])
        self.assertEqual(options["embed"].title, "Worlds · Game stats")
        self.assertEqual(len(options["embed"].fields), 6)
        self.assertEqual([button.label for button in view.game_buttons], ["Game 1"])
        picker = next(item for item in view.children if isinstance(item, discord.ui.Select))
        self.assertEqual([o.label for o in picker.options], ["AAA vs BBB", "OLD vs TEAM"])
        self.assertTrue(picker.options[0].default)
        self.assertEqual(len(view.children), 2)

    async def test_series_change_acknowledges_resets_game_and_clears_attachments(self):
        from bot_app.worlds_stats_views import WorldsStatsView
        from tests.test_worlds_stats import fixture
        series, _, _, _ = fixture()
        second = dict(series, id="second", score=[3, 1], best_of=5)
        view = WorldsStatsView(series, 10, series_list=[series, second])
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                      data={"values": ["1"]}, message=SimpleNamespace(edit=AsyncMock()))

        async def render(number, **kwargs):
            interaction.response.defer.assert_awaited_once()
            self.assertEqual(number, 1)
            self.assertEqual(view.series["id"], "second")
            return {"embed": discord.Embed(title="Worlds · Game stats"), "view": view}

        with patch.object(view, "render_game", side_effect=render):
            await view.choose_series(interaction)
        self.assertEqual(view.selected, 1)
        self.assertEqual([b.label for b in view.game_buttons], ["Game 1", "Game 2", "Game 3", "Game 4"])
        self.assertEqual(interaction.message.edit.call_args.kwargs["attachments"], [])

    async def test_empty_stats_returns_concise_status_without_schedule(self):
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())
        with patch("bot_app.commands.stats.log_command"), \
             patch("bot_app.worlds_command.fetch_stats_series", return_value=[]):
            await StatsCommands.stats.callback(StatsCommands(Mock()), ctx)
        self.assertEqual(ctx.respond.call_args.kwargs["embed"].description, "No Worlds game stats available.")
        self.assertNotIn("view", ctx.respond.call_args.kwargs)

    async def test_stats_is_registered_as_its_own_public_command(self):
        bot = discord.Bot()
        register_all(bot)
        registered = [c for c in bot.pending_application_commands if c.name == "stats"]
        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0].options, [])
        self.assertIn("Worlds", registered[0].description)

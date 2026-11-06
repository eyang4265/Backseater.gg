"""Manual series navigation, spoiler payloads, and acknowledgement before slow work."""

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord

from bot_app.worlds import announcement
from bot_app.worlds_views import schedule_tabs, WorldsScheduleView
from bot_app.worlds_stats_views import WorldsStatsView
from bot_app.worlds_stats import parse_final_stats, game_stats_embed
from bot_app.lolesports import EsportsUnavailable
from tests.test_worlds_stats import fixture


class WorldsPickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 19, tzinfo=timezone.utc)
        self.series, self.game, window, details = fixture()
        self.series.update(state="inProgress", start=self.now.timestamp())
        self.stats = parse_final_stats(window, details, self.series, self.game)

    def interaction(self, choice="0"):
        return SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                               data={"values": [choice]}, channel_id=10,
                               message=SimpleNamespace(id=99, edit=AsyncMock()))

    async def test_picker_reuses_game_view_and_back_clears_chart(self):
        view = WorldsScheduleView(schedule_tabs([self.series], now=self.now))
        interaction = self.interaction()

        def load(*args):
            interaction.response.defer.assert_awaited_once()
            return self.stats

        with patch("bot_app.worlds_stats_views.read_json", return_value={}), \
             patch("bot_app.worlds_stats.load_saved_stats", side_effect=load), \
             patch("bot_app.worlds_game_details.load_game_details", return_value={"bans": {}}), \
             patch("bot_app.charts.build_worlds_damage_share_chart",
                   return_value=discord.File(BytesIO(b"png"), filename="chart.png")):
            await view.series_select.callback(interaction)
        options = interaction.message.edit.call_args.kwargs
        stats_view = options["view"]
        self.assertIsInstance(stats_view, WorldsStatsView)
        self.assertTrue(options["file"].spoiler)
        self.assertIsNone(options["embed"].image)
        self.assertEqual([b.label for b in stats_view.children],
                         ["Game 1", "Game 2", "Game 3", "Back to schedule"])
        back = self.interaction()
        await stats_view.children[-1].callback(back)
        back.response.defer.assert_awaited_once()
        self.assertIs(back.message.edit.call_args.kwargs["view"], view)
        self.assertEqual(back.message.edit.call_args.kwargs["attachments"], [])
        self.assertIs(back.message.edit.call_args.kwargs["embed"], view.render())

    async def test_more_than_25_series_are_all_selectable_and_date_change_resets_menu(self):
        matches = [dict(self.series, id=str(i)) for i in range(50)]
        tomorrow = dict(self.series, id="next", start=self.now.timestamp() + 86400)
        view = WorldsScheduleView(schedule_tabs(matches + [tomorrow], now=self.now))
        self.assertEqual(len(view.series_select.options), 24)
        self.assertTrue(all("AAA" not in option.label for option in view.series_select.options))
        for _ in range(2):
            interaction = self.interaction("more")
            await view.series_select.callback(interaction)
            interaction.response.defer.assert_awaited_once()
        self.assertEqual([o.value for o in view.series_select.options], ["46", "47", "48", "49", "previous"])
        interaction = self.interaction()
        interaction.edit_original_response = AsyncMock()
        await view.day_buttons[-1].callback(interaction)
        self.assertEqual(view.series_page, 0)
        self.assertEqual(view.series_select.options[0].value, "0")
        self.assertEqual(view.series_by_tab[view.selected], [tomorrow])

    async def test_missing_stats_fail_concisely_after_acknowledgement(self):
        schedule = WorldsScheduleView(schedule_tabs([self.series], now=self.now))
        view = WorldsStatsView(self.series, 10, return_view=schedule)
        interaction = self.interaction()

        def fetch(*args):
            interaction.response.defer.assert_awaited_once()
            raise EsportsUnavailable("offline")

        with patch("bot_app.worlds_stats_views.read_json", return_value={}), \
             patch("bot_app.worlds_stats.load_saved_stats", return_value=None), \
             patch("bot_app.worlds_game_details.load_game_details", return_value={"bans": {}}), \
             patch("bot_app.worlds_stats.final_stats", side_effect=fetch):
            await view.change(interaction, 1)
        options = interaction.message.edit.call_args.kwargs
        self.assertIn("Stats unavailable", options["embed"].footer.text)
        self.assertEqual(options["attachments"], [])
        self.assertNotIn("file", options)

    async def test_future_game_never_requests_final_stats(self):
        future = dict(self.series, state="unstarted", score=[0, 0])
        schedule = WorldsScheduleView(schedule_tabs([future], now=self.now))
        view = WorldsStatsView(future, 10, return_view=schedule)
        with patch("bot_app.worlds_stats_views.read_json", return_value={}), \
             patch("bot_app.worlds_stats.load_saved_stats", return_value=None), \
             patch("bot_app.worlds_stats.final_stats") as fetch:
            await view.change(self.interaction(), 1)
        fetch.assert_not_called()

    async def test_default_spoilers_cover_scores_winners_player_fields_and_live_records(self):
        self.series["live_title"] = "Worlds Swiss Stage 2-1"
        live = announcement(self.series, game_start=2)
        self.assertEqual(live.title, "Worlds · Live game")
        self.assertIn("||Worlds Swiss Stage 2-1 · Game 2||", live.description)
        final = announcement(self.series, final=True)
        self.assertTrue(all(line.startswith("||") and line.endswith("||")
                            for line in final.description.split("\n\n")[0].splitlines()))
        with patch("bot_app.worlds_game_details.load_game_details", return_value=None):
            embed = game_stats_embed(self.series, 1, self.stats)
        self.assertEqual([f.name for f in embed.fields[:3]], ["Team 1", "K/D/A", "Damage share"])
        self.assertTrue(all(line.startswith("||") and line.endswith("||")
                            for field in embed.fields for line in field.value.splitlines()))

    async def test_relative_dates_use_pacific_calendar_and_skip_off_days(self):
        now = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)  # Oct 28 in Pacific
        yesterday = dict(self.series, id="past", start=datetime(2026, 10, 27, 18, tzinfo=timezone.utc).timestamp())
        tomorrow = dict(self.series, id="next", start=datetime(2026, 10, 29, 18, tzinfo=timezone.utc).timestamp())
        tabs = schedule_tabs([yesterday, tomorrow], now=now)
        self.assertEqual([label for label, _ in tabs], ["Yesterday", "Today", "Tomorrow"])
        self.assertEqual(tabs[0][1][0].fields[0].name, "Yesterday")
        self.assertIn("Tomorrow", tabs[2][1][0].fields[0].name)
        later = dict(tomorrow, start=tomorrow["start"] + 86400)
        self.assertEqual(schedule_tabs([yesterday, later], now=now)[2][0], "Fri, Oct 30")

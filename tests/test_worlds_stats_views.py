"""Game-count tabs, unused slots, link-free player rows, and acknowledgement order."""

from types import SimpleNamespace

import discord
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot_app import worlds
from bot_app.worlds_stats import game_stats_embed
from bot_app.worlds_stats_views import WorldsStatsView, register_worlds_stats_views, upgrade_stats_messages
from tests.test_worlds_stats import fixture


class GameTabTests(unittest.IsolatedAsyncioTestCase):
    async def test_format_controls_tab_count_and_unused_tabs_show_final_result(self):
        series, _, _, _ = fixture()
        for best_of in (3, 5):
            series = dict(series, best_of=best_of, score=[best_of // 2 + 1, 0], state="completed")
            view = WorldsStatsView(series, 10)
            self.assertEqual([b.label for b in view.children],
                             [f"Game {n}" for n in range(1, best_of + 1)])
            embed = game_stats_embed(series, best_of)
            self.assertEqual(embed.title, "Worlds · Final result")
            self.assertIn("wins the series", embed.description)
            self.assertIsNone(embed.url)
            self.assertNotIn("https://", embed.description)
            self.assertTrue(view.is_persistent())

    async def test_names_precede_dot_and_champion_without_broadcast_links(self):
        from bot_app.worlds_stats import parse_final_stats
        series, game, window, details = fixture()
        data = parse_final_stats(window, details, series, game)
        with patch("bot_app.worlds_stats.champion_emoji", return_value="<:Ahri:123>"):
            embed = game_stats_embed(series, 1, data)
        self.assertEqual(embed.fields[0].value.splitlines()[0], "Player 1 · <:Ahri:123>")
        self.assertEqual(len(embed.fields), 6)
        self.assertNotIn("https://", embed.description)
        self.assertIsNone(embed.url)

    async def test_team_tags_are_removed_without_truncating_player_names(self):
        from bot_app.worlds_stats import parse_final_stats
        from bot_app.worlds_player_names import player_name
        series, game, window, details = fixture()
        window["gameMetadata"]["blueTeamMetadata"]["participantMetadata"][0]["summonerName"] = "AAA Player One"
        data = parse_final_stats(window, details, series, game)
        with patch("bot_app.worlds_stats.champion_emoji", return_value="<:Ahri:123>"):
            embed = game_stats_embed(series, 1, data)
        self.assertEqual(embed.fields[0].value.splitlines()[0], "Player One · <:Ahri:123>")
        self.assertEqual(player_name("Alpha Player One", series, "a"), "Player One")
        self.assertEqual(player_name("Player One", series, "a"), "Player One")
        self.assertEqual(player_name("BBB Player One", series, "a"), "BBB Player One")

    async def test_unplayed_tabs_show_current_score_during_ongoing_series(self):
        series, _, _, _ = fixture()
        series.update(state="inProgress", score=[1, 1], best_of=5)
        for number in (3, 4, 5):
            embed = game_stats_embed(series, number)
            self.assertEqual(embed.title, "Worlds · Series result")
            self.assertIn("1–1", embed.description)
            self.assertNotIn("wins", embed.description)
            self.assertEqual(len(embed.fields), 0)
            self.assertIsNone(embed.image)
            self.assertNotIn("https://", embed.description)

    async def test_switch_defers_before_storage_and_uses_latest_series_result(self):
        series, _, _, _ = fixture()
        view = WorldsStatsView(series, 10)
        latest = dict(series, state="completed", score=[2, 0])
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                      message=SimpleNamespace(edit=AsyncMock()))

        def read(*args):
            interaction.response.defer.assert_awaited_once()
            return {"channels": {"10": {"series": {series["id"]: {"series_snapshot": latest}}}}}

        with patch("bot_app.worlds_stats_views.read_json", side_effect=read):
            await view.children[2].callback(interaction)
        options = interaction.message.edit.call_args.kwargs
        self.assertEqual(options["embed"].title, "Worlds · Final result")
        self.assertEqual(options["attachments"], [])
        self.assertEqual(view.selected, 3)
        self.assertTrue(view.children[2].disabled)

    async def test_restart_does_not_restore_automatic_stats_controls(self):
        series, _, _, _ = fixture()
        bot = SimpleNamespace(add_view=Mock())
        state = {"channels": {"10": {"series": {series["id"]: {
            "series_snapshot": series, "stats_games": {"123": {"message_id": 99, "final": True}}}}}}}
        with patch("bot_app.worlds_stats_views.read_json", return_value=state):
            self.assertEqual(register_worlds_stats_views(bot), 0)
        bot.add_view.assert_not_called()

    async def test_unused_tab_sends_empty_attachments_through_real_discord_serializer(self):
        series, _, _, _ = fixture()
        series.update(state="completed", score=[2, 0])
        view = WorldsStatsView(series, 10)
        http = SimpleNamespace(edit_message=AsyncMock(return_value={}))
        state = SimpleNamespace(http=http, allowed_mentions=None,
                                prevent_view_updates_for=Mock(), store_view=Mock())
        source = SimpleNamespace(_state=state, id=99, channel=SimpleNamespace(id=10))
        edit_message = discord.Message.edit

        async def edit(**options):
            return await edit_message(source, **options)

        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                      message=SimpleNamespace(edit=edit))
        with patch("bot_app.worlds_stats_views.read_json", return_value={"channels": {}}), \
             patch("discord.message.Message", return_value=SimpleNamespace(id=99)):
            await view.children[2].callback(interaction)
        interaction.response.defer.assert_awaited_once()
        payload = http.edit_message.call_args.kwargs
        self.assertEqual(payload["attachments"], [])
        self.assertEqual(payload["embeds"][0]["title"], "Worlds · Final result")
        self.assertNotIn("image", payload["embeds"][0])

    async def test_upgrade_adds_tabs_once_without_reposting_results(self):
        series, game, _, _ = fixture()
        message = SimpleNamespace(edit=AsyncMock(), embeds=[])
        channel = SimpleNamespace(id=10, fetch_message=AsyncMock(return_value=message))
        receipt = {"stats_games": {game["id"]: {"message_id": 99, "status": "unavailable", "final": True}}}
        save = AsyncMock()
        with patch("bot_app.worlds_stats.load_saved_stats", return_value=None):
            await upgrade_stats_messages(channel, series, receipt, save)
            await upgrade_stats_messages(channel, series, receipt, save)
        message.edit.assert_awaited_once()
        self.assertEqual(len(message.edit.call_args.kwargs["view"].children), 3)
        self.assertIsNone(message.edit.call_args.kwargs["embed"].url)

    async def test_upgrade_removes_stats_tabs_and_attachments_from_earlier_score_posts(self):
        series, game, _, _ = fixture()
        message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(id=10, fetch_message=AsyncMock(return_value=message))
        receipt = {"stats_games": {game["id"]: {"message_id": 99, "final": False}}}
        await upgrade_stats_messages(channel, series, receipt, AsyncMock())
        options = message.edit.call_args.kwargs
        self.assertIsNone(options["view"])
        self.assertEqual(options["attachments"], [])
        self.assertEqual(options["embed"].title, "Worlds · Game result")
        self.assertNotIn("https://", options["embed"].description)
        self.assertEqual(len(options["embed"].fields), 0)

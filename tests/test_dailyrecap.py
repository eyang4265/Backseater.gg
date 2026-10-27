"""Published snapshots, server-scoped routing, and deferred recap replay."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app import daily_summary as daily
from bot_app.commands.dailyrecap import DailyRecapCommands
from bot_app.command_views import register_persistent_command_views
from tests.test_daily_summary import announcement, at


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_buttons_acknowledge_before_edit_and_restore_selected_page(self):
        embeds = [discord.Embed(title="First"), discord.Embed(title="Second")]
        embeds[1].add_field(name="Player", value="One#NA1\nTwo#NA1", inline=True)
        embeds[1].add_field(name="G", value="3\n2", inline=True)
        embeds[1].add_field(name="W/L · LP", value="1 / 0 · +18\n- · —", inline=True)
        view = daily.RecapPaginator(embeds)
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                      edit_original_response=AsyncMock(),
                                      message=SimpleNamespace(id=42), channel_id=7)
        async def edit(**kwargs):
            interaction.response.defer.assert_awaited_once()
            self.assertEqual(kwargs["embed"].title, "Second")
        interaction.edit_original_response.side_effect = edit
        with patch.object(daily, "remember_embed_button_state") as remember:
            await view.next_button.callback(interaction)
        self.assertTrue(view.is_persistent())
        self.assertEqual(view.render().fields[0].value, "One (3)\nTwo (2)")
        self.assertEqual(view.render().fields[1].value, "1/0\n\u200b")
        self.assertEqual(view.render().fields[2].value, "+18\n\u200b")
        self.assertEqual(embeds[1].fields[2].value, "1 / 0 · +18\n- · —")
        self.assertTrue(view.next_button.disabled)
        self.assertFalse(view.previous_button.disabled)
        message_id, channel_id, kind, payload = remember.call_args.args
        self.assertEqual((message_id, channel_id, kind, payload["page"]), (42, 7, "daily_recap", 1))
        bot = Mock()
        with patch("bot_app.command_views.load_embed_button_states", return_value=[
            {"message_id": message_id, "channel_id": channel_id, "kind": kind, "payload": payload},
        ]):
            self.assertEqual(register_persistent_command_views(bot), 1)
        restored = bot.add_view.call_args.args[0]
        self.assertEqual(restored.render().title, "Second")
        self.assertEqual(restored.render().to_dict(), view.render().to_dict())
        self.assertTrue(restored.is_persistent())
        self.assertEqual([item.custom_id for item in restored.children],
                         [item.custom_id for item in view.children])

    def test_legacy_columns_upgrade_without_changing_saved_state(self):
        embed = discord.Embed(title="Daily Recap")
        for name, value in [("Player", "Player#NA1\nOther#EUW (TFT)"),
                            ("Record", "2 / 1 W / L · 1 unavailable\n0 / 0 Top 4 / Bottom 4 · 3 unavailable"),
                            ("Recorded LP change", "+18 (1 games)\n—")]:
            embed.add_field(name=name, value=value, inline=True)
        state = {"channels": {"1": {"latest": {"embeds": [embed.to_dict()]}}}}
        with patch.object(daily, "read_json", return_value=state), patch.object(daily, "write_json") as write:
            result = daily.latest_recap(1)[0]
        self.assertEqual([f.name for f in result.fields], ["Player (Games)", "W/L", "LP"])
        self.assertEqual([f.value for f in result.fields],
                         ["Player (4)\nOther (TFT) (3)", "2/1\n\u200b", "+18\n\u200b"])
        self.assertEqual(state["channels"]["1"]["latest"]["embeds"][0], embed.to_dict())
        write.assert_not_called()
        # Four-column snapshots use the same compact replay as older records.
        embed.clear_fields()
        for name, value in [("Player", "Player#NA1\nOther#EUW (TFT)"), ("Games", "4\n3"),
                            ("W/L", "2 / 1 · 1 unavailable\n0 / 0 · 3 unavailable"),
                            ("LP Change", "+18\n—")]:
            embed.add_field(name=name, value=value, inline=True)
        state["channels"]["1"]["latest"]["embeds"] = [embed.to_dict()]
        with patch.object(daily, "read_json", return_value=state):
            upgraded = daily.latest_recap(1)[0]
        self.assertEqual(upgraded.to_dict(), result.to_dict())
        self.assertEqual(state["channels"]["1"]["latest"]["embeds"][0], embed.to_dict())

    async def test_snapshot_matches_post_and_survives_pruning_and_partial_new_day(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(daily, "STATE_PATH", Path(directory)/"daily.json"), \
             patch.object(daily, "get_settings", return_value=SimpleNamespace(announcement_channel_id=1)), \
             patch.object(daily, "load_guild_channels", return_value={}):
            channel = SimpleNamespace(id=1, send=AsyncMock())
            bot = SimpleNamespace(get_channel=Mock(return_value=channel))
            await daily.poll_daily_summary(bot, now=at(12, 6))
            daily.record_announcement(1, announcement(), now=at(12, 7))
            self.assertEqual(daily.latest_recap(1), [])
            await daily.poll_daily_summary(bot, now=at(13, 4))
            saved = daily.latest_recap(1)
            self.assertEqual([e.to_dict() for e in saved], [channel.send.await_args.kwargs["embed"].to_dict()])
            self.assertEqual(daily.latest_recap(2), [])
            daily._mark_page(1, "2026-10-13", 1)
            self.assertEqual([e.to_dict() for e in daily.latest_recap(1)], [e.to_dict() for e in saved])
            daily.record_announcement(1, announcement("later"), now=at(13)+timedelta(days=30))
            self.assertNotIn("2026-10-12", daily._state(0)["channels"]["1"]["sent"])
            self.assertEqual([e.to_dict() for e in daily.latest_recap(1)], [e.to_dict() for e in saved])


class DailyRecapCommandTests(unittest.IsolatedAsyncioTestCase):
    def context(self, guild_id=10):
        return SimpleNamespace(author=SimpleNamespace(id=1), channel=SimpleNamespace(id=100),
                               guild=SimpleNamespace(id=guild_id) if guild_id else None,
                               defer=AsyncMock(), respond=AsyncMock())

    async def invoke(self, ctx, bot=None):
        await DailyRecapCommands.dailyrecap.callback(DailyRecapCommands(bot or Mock()), ctx)

    async def test_current_channel_defers_before_read_and_replays_pages(self):
        ctx = self.context()
        embeds = daily.summary_embeds([], *daily.completed_window(at(13)))
        # Duplicate pages are enough to exercise the shared paginator without changing saved content.
        def read(channel_id):
            ctx.defer.assert_awaited_once()
            self.assertEqual(channel_id, 100)
            return embeds * 2
        with patch("bot_app.commands.dailyrecap.log_command"), \
             patch("bot_app.commands.dailyrecap.latest_recap", side_effect=read), \
             patch("bot_app.commands.dailyrecap.load_guild_channels") as routes:
            await self.invoke(ctx)
        routes.assert_not_called()
        result = ctx.respond.await_args.kwargs
        self.assertEqual(result["embed"].to_dict(), embeds[0].to_dict())
        self.assertIsNotNone(result["view"])
        self.assertEqual(result["view"].max_page, 1)

    async def test_other_channel_uses_server_route(self):
        ctx = self.context()
        embeds = daily.summary_embeds([], *daily.completed_window(at(13)))
        with patch("bot_app.commands.dailyrecap.log_command"), \
             patch("bot_app.commands.dailyrecap.latest_recap", side_effect=[[], embeds]) as read, \
             patch("bot_app.commands.dailyrecap.load_guild_channels", return_value={"10": 200}):
            await self.invoke(ctx)
        self.assertEqual([c.args[0] for c in read.call_args_list], [100, 200])
        self.assertEqual(ctx.respond.await_args.kwargs["embed"].to_dict(), embeds[0].to_dict())
        self.assertNotIn("view", ctx.respond.await_args.kwargs)

    async def test_fallback_does_not_expose_another_servers_recap(self):
        for fallback_guild, expected in [(10, [100, 200]), (11, [100])]:
            ctx = self.context()
            bot = SimpleNamespace(get_channel=Mock(return_value=SimpleNamespace(id=200, guild=SimpleNamespace(id=fallback_guild))))
            with patch("bot_app.commands.dailyrecap.log_command"), \
                 patch("bot_app.commands.dailyrecap.latest_recap", return_value=[]) as read, \
                 patch("bot_app.commands.dailyrecap.load_guild_channels", return_value={}), \
                 patch("bot_app.commands.dailyrecap.get_settings", return_value=SimpleNamespace(announcement_channel_id=200)):
                await self.invoke(ctx, bot)
            self.assertEqual([c.args[0] for c in read.call_args_list], expected)
            self.assertIn("No daily recap", ctx.respond.await_args.kwargs["embed"].description)

    async def test_dm_explains_server_requirement_without_reading_state(self):
        ctx = self.context(None)
        with patch("bot_app.commands.dailyrecap.log_command"), patch("bot_app.commands.dailyrecap.latest_recap") as read:
            await self.invoke(ctx)
        ctx.defer.assert_awaited_once()
        read.assert_not_called()
        self.assertTrue(ctx.respond.await_args.kwargs["ephemeral"])

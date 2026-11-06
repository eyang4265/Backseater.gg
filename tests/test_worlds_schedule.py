"""Daily schedule timing, stable numbered subscriptions, and targeted start pings."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app import worlds_schedule as daily
from bot_app.worlds import announcement
from tests.test_worlds import event, parse


class DailyScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.message = SimpleNamespace(id=456, add_reaction=AsyncMock(), edit=AsyncMock(), reactions=[])
        self.channel = SimpleNamespace(id=123, send=AsyncMock(return_value=self.message),
                                       fetch_message=AsyncMock(return_value=self.message))
        self.bot = SimpleNamespace(get_channel=lambda _: self.channel)
        self.now = datetime(2026, 10, 28, 11, tzinfo=timezone.utc)  # 04:00 Pacific
        first = parse(event())[0]
        second = dict(first, id="series-2", start=first["start"] + 10800)
        tomorrow = dict(first, id="tomorrow", start=first["start"] + 86400)
        self.series = [second, tomorrow, first]
        self.fetch = Mock(return_value=self.series)
        patches = [patch.object(daily, "STATE_PATH", Path(self.temp.name) / "daily.json"),
                   patch.object(daily, "resolve_worlds_channels", new=AsyncMock(return_value={123})),
                   patch("bot_app.worlds.fetch_series", self.fetch)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_no_post_before_four_and_once_after_four(self):
        await daily.poll_worlds_schedule(self.bot, now=datetime(2026, 10, 28, 10, 59, tzinfo=timezone.utc))
        self.fetch.assert_not_called()
        await daily.poll_worlds_schedule(self.bot, now=self.now)
        await daily.poll_worlds_schedule(self.bot, now=self.now)
        self.channel.send.assert_awaited_once()
        self.assertEqual([call.args[0] for call in self.message.add_reaction.call_args_list], ["1️⃣", "2️⃣"])
        saved = daily.read_json(daily.STATE_PATH, {})["channels"]["123"]["2026-10-28"]
        self.assertEqual(saved["series"], {"1️⃣": "series-1", "2️⃣": "series-2"})
        self.assertTrue(saved["ready"])
        embed = self.channel.send.call_args.kwargs["embed"]
        self.assertIn("1️⃣", embed.fields[0].value)
        self.assertIn("2️⃣", embed.fields[0].value)
        self.assertEqual(len(embed.fields), 3)
        self.assertNotIn("Next games", str(embed.to_dict()))
        self.assertNotIn("No later games", embed.description)

    async def test_existing_post_is_updated_without_reposting_or_resetting_subscriptions(self):
        await daily.poll_worlds_schedule(self.bot, now=self.now)
        state = daily.read_json(daily.STATE_PATH, {})
        receipt = state["channels"]["123"]["2026-10-28"]
        receipt.pop("layout")
        daily.write_json(daily.STATE_PATH, state)
        await daily.poll_worlds_schedule(self.bot, now=self.now)
        self.channel.send.assert_awaited_once()
        self.message.edit.assert_awaited_once()
        self.assertEqual(len(self.message.edit.call_args.kwargs["embed"].fields), 3)
        self.assertEqual(self.message.add_reaction.await_count, 2)
        saved = daily.read_json(daily.STATE_PATH, {})["channels"]["123"]["2026-10-28"]
        self.assertEqual(saved["layout"], daily.SCHEDULE_LAYOUT_VERSION)

    async def test_off_day_only_reports_today_without_next_day(self):
        self.fetch.return_value = [s for s in self.series if s["id"] == "tomorrow"]
        await daily.poll_worlds_schedule(self.bot, now=self.now)
        embed = self.channel.send.call_args.kwargs["embed"]
        self.assertIn("No Worlds series scheduled", embed.fields[0].value)
        self.assertEqual(len(embed.fields), 1)
        self.message.add_reaction.assert_not_awaited()

    async def test_partial_reaction_setup_resumes_without_repost(self):
        failure = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "blocked")
        self.message.add_reaction.side_effect = [None, failure]
        with self.assertLogs(daily.LOGGER, level="ERROR"):
            await daily.poll_worlds_schedule(self.bot, now=self.now)
        self.message.add_reaction.side_effect = None
        await daily.poll_worlds_schedule(self.bot, now=self.now)
        self.channel.send.assert_awaited_once()
        self.assertEqual([call.args[0] for call in self.message.add_reaction.call_args_list], ["1️⃣", "2️⃣", "2️⃣"])

    async def test_catchup_posts_today_not_prior_days(self):
        await daily.poll_worlds_schedule(self.bot, now=datetime(2026, 10, 28, 22, tzinfo=timezone.utc))
        saved = daily.read_json(daily.STATE_PATH, {})["channels"]["123"]
        self.assertEqual(list(saved), ["2026-10-28"])

    async def test_reactors_are_series_specific_current_and_exclude_bots(self):
        await daily.poll_worlds_schedule(self.bot, now=self.now)

        def reaction(emoji, ids):
            async def users():
                for user_id, bot in ids:
                    yield SimpleNamespace(id=user_id, bot=bot)
            return SimpleNamespace(emoji=emoji, users=users)

        self.message.reactions = [reaction("1️⃣", [(10, False), (11, True)]), reaction("2️⃣", [(20, False)])]
        self.assertEqual(await daily.series_subscribers(self.channel, "series-1"), [10])
        self.assertEqual(await daily.series_subscribers(self.channel, "series-2"), [20])
        self.message.reactions = []  # Users removed their reactions before the start.
        self.assertEqual(await daily.series_subscribers(self.channel, "series-1"), [])

    async def test_deleted_post_does_not_block_start(self):
        await daily.poll_worlds_schedule(self.bot, now=self.now)
        self.channel.fetch_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "gone")
        self.assertEqual(await daily.series_subscribers(self.channel, "series-1"), [])

    async def test_large_ping_groups_retry_only_unsent_batches(self):
        users = list(range(1000, 1065))
        receipt, save = {}, AsyncMock()
        series = self.series[0]
        failure = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "blocked")
        self.channel.send.side_effect = [self.message, failure]
        with patch.object(daily, "series_subscribers", new=AsyncMock(return_value=users)):
            with self.assertRaises(discord.Forbidden):
                await daily.send_start_alert(self.channel, series, announcement(series), receipt, save)
        self.assertEqual(receipt["start_batches"], 1)
        self.channel.send.reset_mock()
        self.channel.send.side_effect = None
        await daily.send_start_alert(self.channel, series, announcement(series), receipt, save)
        self.channel.send.assert_awaited_once()
        options = self.channel.send.call_args.kwargs
        self.assertNotIn("embed", options)
        self.assertEqual([user.id for user in options["allowed_mentions"].users], users[60:])
        self.assertFalse(options["allowed_mentions"].everyone)
        self.assertFalse(options["allowed_mentions"].roles)

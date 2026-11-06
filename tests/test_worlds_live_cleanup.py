"""Ended-series cleanup, legacy notice matching, and durable deletion retries."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from bot_app.worlds_live_cleanup import delete_series_live_notices
from tests.test_worlds_stats import fixture


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_scan_deletes_only_this_bots_matching_live_and_score_posts(self):
        series, _, _, _ = fixture()
        series.update(state="completed", score=[2, 0])
        bot = SimpleNamespace(user=SimpleNamespace(id=7))

        def message(id, title, description="AAA vs BBB", author=7):
            return SimpleNamespace(id=id, author=SimpleNamespace(id=author),
                                   embeds=[discord.Embed(title=title, description=description)])

        messages = [message(1, "Worlds · Game 3 live"),
                    message(2, "Worlds · Game 3 live", author=8),
                    message(3, "Worlds · Game 3 live", description="CCC vs DDD"),
                    message(4, "Worlds · Final result · Game 3"),
                    message(5, "Worlds · Schedule"),
                    message(6, "Worlds · Game result", "AAA 1–0 BBB"),
                    message(7, "Worlds · Game 2 result", "AAA 1–1 BBB"),
                    message(8, "Worlds · Game result", "CCC 1–0 DDD"),
                    message(9, "Worlds · Game result", "AAA 1–0 BBB", author=8),
                    message(10, "Worlds · Final result", "AAA 2–0 BBB")]

        async def history(**kwargs):
            for item in messages:
                yield item

        delete = AsyncMock()
        channel = SimpleNamespace(history=Mock(side_effect=history),
                                  get_partial_message=Mock(return_value=SimpleNamespace(delete=delete)))
        receipt = {"final": True}
        await delete_series_live_notices(bot, channel, series, receipt, AsyncMock())
        self.assertEqual([call.args for call in channel.get_partial_message.call_args_list],
                         [(1,), (6,), (7,)])
        self.assertEqual(delete.await_count, 3)
        self.assertEqual(receipt["live_message_ids"], [])
        self.assertTrue(receipt["live_notice_scan_done"])

    async def test_failed_deletion_retains_receipt_and_missing_message_counts_as_deleted(self):
        series, _, _, _ = fixture()
        series["state"] = "completed"
        failure = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        missing = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "gone")
        delete = AsyncMock(side_effect=[failure, missing])
        channel = SimpleNamespace(get_partial_message=Mock(return_value=SimpleNamespace(delete=delete)))
        receipt = {"final": True, "live_notice_scan_done": True, "live_message_ids": [1]}
        with self.assertRaises(discord.Forbidden):
            await delete_series_live_notices(None, channel, series, receipt, AsyncMock())
        self.assertEqual(receipt["live_message_ids"], [1])
        await delete_series_live_notices(None, channel, series, receipt, AsyncMock())
        self.assertEqual(receipt["live_message_ids"], [])

    async def test_no_cleanup_until_series_finished_and_final_post_delivered(self):
        series, _, _, _ = fixture()
        channel = SimpleNamespace(get_partial_message=Mock())
        receipt = {"final": False, "live_message_ids": [1]}
        for state, final in (("inProgress", True), ("completed", False)):
            series["state"], receipt["final"] = state, final
            await delete_series_live_notices(None, channel, series, receipt, AsyncMock())
        channel.get_partial_message.assert_not_called()

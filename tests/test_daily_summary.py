"""Gaming-day boundaries, result accuracy, restart deduplication, and retries."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app import daily_summary as daily
from bot_app.announcement_models import MatchAnnouncement


def announcement(match_id="one", *, unavailable=False, kind="lol", win=True, lp="+18 LP"):
    return MatchAnnouncement("", None, {"metadata": {"matchId": match_id}, "info": {"participants": [
        {"puuid": "a", "riotIdGameName": "Player", "riotIdTagline": "NA1", "win": win, "placement": 2},
    ]}}, {"a"}, game_type=kind, lp_changes={"a": lp}, result_unavailable=unavailable)


def at(day, hour=12, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=daily.PACIFIC)


class DailySummaryTests(unittest.TestCase):
    def test_mayhem_names_include_registered_teammates_and_recover_old_events(self):
        tracked = SimpleNamespace(puuid="a", riot_id="Tracked#NA1")
        teammate = SimpleNamespace(puuid="b", riot_id="Teammate#NA1")
        item = announcement(unavailable=True, lp="")
        item.match["info"].update(queueId=2400, participants=[{"puuid": "a"}, {"puuid": "b"}])
        item.highlight_puuids.add("b")
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(daily, "STATE_PATH", Path(directory)/"daily.json"), \
             patch.object(daily, "load_accounts", return_value={"owner": tracked}), \
             patch.object(daily, "load_teammates", return_value={"b": teammate}), \
             patch.object(daily, "load_tft_accounts") as tft:
            daily.record_announcement(1, item, now=at(12), names={"a": tracked.riot_id})
            events = list(daily._state(0)["channels"]["1"]["events"].values())
            self.assertEqual({p["puuid"]: p["name"] for p in events[0]["players"]},
                             {"a": "Tracked#NA1", "b": "Teammate#NA1"})
            for player in events[0]["players"]:
                player["name"] = "Unknown player"
            embed = daily.summary_embeds(events, *daily.completed_window(at(13)))[0]
            self.assertEqual(set(embed.fields[0].value.splitlines()), {"Tracked (1)", "Teammate (1)"})
            self.assertEqual(embed.fields[2].value, "\u200b\n\u200b")
            tft.assert_not_called()

    def test_four_am_window_and_dst(self):
        start, end = daily.completed_window(at(13, 3, 59))
        self.assertEqual((start.day, end.day, end.hour), (11, 12, 4))
        start, end = daily.completed_window(at(13, 4))
        self.assertEqual((start.day, end.day), (12, 13))
        for now, hours in [(datetime(2026, 3, 8, 4, tzinfo=daily.PACIFIC), 23),
                           (datetime(2026, 11, 1, 4, tzinfo=daily.PACIFIC), 25)]:
            start, end = daily.completed_window(now)
            self.assertEqual((end.timestamp()-start.timestamp())/3600, hours)

    def test_journal_channel_dedup_and_unavailable_results(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(daily, "STATE_PATH", Path(directory)/"daily.json"):
            daily.record_announcement(1, announcement(), now=at(12))
            daily.record_announcement(1, announcement(), now=at(12, 13))
            daily.record_announcement(1, announcement("missing", unavailable=True), now=at(12, 14))
            daily.record_announcement(2, announcement(), now=at(12))
            state = daily._state(0)
            self.assertEqual(len(state["channels"]["1"]["events"]), 2)
            self.assertEqual(len(state["channels"]["2"]["events"]), 1)
            events = list(state["channels"]["1"]["events"].values())
            start, end = daily.completed_window(at(13, 4))
            embed = daily.summary_embeds(events, start, end)[0]
            self.assertEqual([f.name for f in embed.fields], ["Player (Games)", "W/L", "LP"])
            self.assertEqual(embed.fields[1].value, "1/0")
            self.assertEqual(embed.fields[2].value, "+18")
            self.assertNotIn("unavailable", str(embed.to_dict()))
            unknown = daily.summary_embeds([events[1]], start, end)[0]
            self.assertEqual(unknown.fields[1].value, "\u200b")
            self.assertEqual(unknown.fields[2].value, "\u200b")

    def test_pagination_and_tft_separation(self):
        players = [{"puuid": str(i), "name": "_*"*100, "win": False, "placement": 2, "lp": None} for i in range(50)]
        events = [{"game_type": "tft", "unavailable": False, "players": players}]
        start, end = daily.completed_window(at(13))
        embeds = daily.summary_embeds(events, start, end)
        self.assertEqual(len(embeds), 7)
        self.assertTrue(all(len(f.value) <= 1024 for e in embeds for f in e.fields))
        self.assertEqual(embeds[0].fields[1].value.splitlines()[0], "1/0")
        self.assertEqual(embeds[0].fields[2].value.splitlines()[0], "\u200b")
        self.assertIn("(TFT)", embeds[0].fields[0].value)


class DailyPollTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state_patch = patch.object(daily, "STATE_PATH", Path(self.directory.name)/"daily.json")
        self.state_patch.start()
        self.settings_patch = patch.object(daily, "get_settings", return_value=SimpleNamespace(announcement_channel_id=1))
        self.settings_patch.start()
        self.routes_patch = patch.object(daily, "load_guild_channels", return_value={"guild": 2})
        self.routes_patch.start()
        self.view_patch = patch.object(daily, "remember_recap_view", new=AsyncMock())
        self.remember_view = self.view_patch.start()
        self.channels = {i: SimpleNamespace(id=i, send=AsyncMock()) for i in (1, 2)}
        self.bot = SimpleNamespace(get_channel=lambda i: self.channels[i], fetch_channel=AsyncMock())

    async def asyncTearDown(self):
        self.view_patch.stop()
        self.routes_patch.stop()
        self.settings_patch.stop()
        self.state_patch.stop()
        self.directory.cleanup()

    async def test_once_per_channel_and_no_initial_backfill(self):
        await daily.poll_daily_summary(self.bot, now=at(12, 12))
        self.channels[1].send.assert_not_awaited()
        daily.record_announcement(1, announcement(), now=at(12, 13))
        await daily.poll_daily_summary(self.bot, now=at(13, 3, 59))
        self.channels[1].send.assert_not_awaited()
        await daily.poll_daily_summary(self.bot, now=at(13, 4))
        await daily.poll_daily_summary(self.bot, now=at(13, 6))
        self.channels[1].send.assert_awaited_once()
        self.channels[2].send.assert_awaited_once()
        self.assertNotIn("view", self.channels[1].send.await_args.kwargs)
        self.assertIn("No completed-game", self.channels[2].send.await_args.kwargs["embed"].description)
        self.assertEqual(daily._state(0)["channels"]["1"]["sent"]["2026-10-12"], "done")

    async def test_failed_channel_retries_without_reposting_success(self):
        await daily.poll_daily_summary(self.bot, now=at(12, 6))
        daily.record_announcement(1, announcement(), now=at(12))
        error = discord.HTTPException(Mock(status=500, reason="failure"), "failed")
        self.channels[2].send.side_effect = error
        with self.assertLogs(daily.LOGGER, level="ERROR"):
            await daily.poll_daily_summary(self.bot, now=at(13, 4))
        self.channels[2].send.side_effect = None
        await daily.poll_daily_summary(self.bot, now=at(13, 6))
        self.assertEqual(self.channels[1].send.await_count, 1)
        self.assertEqual(self.channels[2].send.await_count, 2)

    async def test_boundary_event_is_for_next_gaming_day(self):
        daily.record_announcement(1, announcement("old"), now=at(12, 6))
        daily.record_announcement(1, announcement("new"), now=at(13, 4))
        await daily.poll_daily_summary(self.bot, now=at(13, 4))
        embed = self.channels[1].send.await_args.kwargs["embed"]
        self.assertEqual(embed.fields[2].value, "+18")
        self.assertFalse(embed.description)

    async def test_downtime_catches_up_closed_days_once(self):
        await daily.poll_daily_summary(self.bot, now=at(12, 6))
        daily.record_announcement(1, announcement(), now=at(12, 7))
        await daily.poll_daily_summary(self.bot, now=at(15, 3))
        self.assertEqual(self.channels[1].send.await_count, 2)
        await daily.poll_daily_summary(self.bot, now=at(15, 4))
        self.assertEqual(self.channels[1].send.await_count, 3)
        await daily.poll_daily_summary(self.bot, now=at(15, 6))
        self.assertEqual(self.channels[1].send.await_count, 3)

    async def test_oldest_receipt_survives_before_four_am_and_journal_pruning(self):
        await daily.poll_daily_summary(self.bot, now=at(12, 6))
        await daily.poll_daily_summary(self.bot, now=at(26, 3))
        self.assertEqual(self.channels[1].send.await_count, 13)
        # The next calendar date must not expire a still-eligible gaming day.
        await daily.poll_daily_summary(self.bot, now=at(27, 2))
        self.assertEqual(self.channels[1].send.await_count, 14)
        daily.record_announcement(1, announcement(), now=at(27, 3))
        await daily.poll_daily_summary(self.bot, now=at(27, 3))
        await daily.poll_daily_summary(self.bot, now=at(27, 3, 59))
        self.assertEqual(self.channels[1].send.await_count, 14)
        self.assertEqual(daily._state(0)["channels"]["1"]["sent"]["2026-10-12"], "done")
        await daily.poll_daily_summary(self.bot, now=at(27, 4))
        self.assertEqual(self.channels[1].send.await_count, 15)
        self.assertNotIn("2026-10-12", daily._state(0)["channels"]["1"]["sent"])
        await daily.poll_daily_summary(self.bot, now=at(27, 6))
        self.assertEqual(self.channels[1].send.await_count, 15)

    async def test_multipage_recap_retries_one_message_with_persistent_buttons(self):
        await daily.poll_daily_summary(self.bot, now=at(12, 6))
        for i in range(10):
            item = announcement(str(i))
            item.match["info"]["participants"][0]["puuid"] = str(i)
            item.highlight_puuids.clear()
            item.highlight_puuids.add(str(i))
            daily.record_announcement(1, item, now=at(12, 7))
        error = discord.HTTPException(Mock(status=500, reason="failure"), "failed")
        self.channels[1].send.side_effect = error
        with self.assertLogs(daily.LOGGER, level="ERROR"):
            await daily.poll_daily_summary(self.bot, now=at(13, 4))
        self.assertNotIn("2026-10-12", daily._state(0)["channels"]["1"]["sent"])
        self.channels[1].send.side_effect = None
        await daily.poll_daily_summary(self.bot, now=at(13, 6))
        await daily.poll_daily_summary(self.bot, now=at(13, 7))
        self.assertEqual(self.channels[1].send.await_count, 2)
        options = self.channels[1].send.await_args.kwargs
        self.assertIn("Page 1/2", options["embed"].footer.text)
        self.assertEqual(len(options["view"].items), 2)
        self.assertTrue(options["view"].is_persistent())
        self.remember_view.assert_awaited_once()
        self.assertEqual(daily._state(0)["channels"]["1"]["sent"]["2026-10-12"], "done")

"""Offline coverage for the public esports schema and durable delivery receipts."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app import worlds
from tests.test_bracket import swiss_fixture


def event(score=(0, 0), state="unstarted", *, league="worlds"):
    """Minimal server-rendered EventMatch with game states and cumulative wins."""
    return {"__typename": "EventMatch", "id": "series-1", "league": {"slug": league},
            "startTime": "2026-10-28T20:00:00Z", "state": state, "blockName": "Swiss",
            "matchTeams": [{"teamId": name, "code": name, "result": {"gameWins": wins}}
                           for name, wins in zip(("AAA", "BBB"), score)],
            "match": {"strategy": {"count": 3},
                      "games": [{"state": "completed"} for _ in range(sum(score))]}}


def parse(payload):
    """Use the same compact embedded JSON format as the live website."""
    return worlds.parse_series('<script>data.push(' + json.dumps(payload, separators=(",", ":")) + ')</script>')


class ParsingTests(unittest.TestCase):
    def test_announcements_use_official_names_with_the_requested_team_emoji_aliases(self):
        series = parse(event((2, 0), "completed"))[0]
        series.update(names=["TLAW", "TES"], team_names=["Team Liquid Alienware", "TOP ESPORTS"])
        icons = {"TL": "<:TL:123>", "TOP": "<:TOP:456>"}
        with patch.object(worlds, "named_emoji", side_effect=lambda name: icons.get(name)):
            embed = worlds.announcement(series, final=True)
            self.assertIn("<:TL:123> Team Liquid Alienware 2–0 <:TOP:456> TOP ESPORTS", embed.description)
            self.assertIn("Team Liquid Alienware wins the series", embed.description)
            self.assertEqual(worlds.team_label(series, 0), "<:TL:123> TLAW")

    def test_official_name_fallback_and_geng_alias(self):
        series = parse(event())[0]
        series.update(names=["GEN", "TBD"], team_names=["Gen.G Esports", "TBD"])
        with patch.object(worlds, "named_emoji", return_value=None) as lookup:
            self.assertEqual(worlds.team_label(series, 0, official=True), "Gen.G Esports")
            lookup.assert_called_once_with("GenG")

    def test_live_titles_use_entering_swiss_record(self):
        payloads = []
        for series in swiss_fixture():
            payload = event(series["score"], series["state"])
            payload.update(id=series["id"], startTime=datetime.fromtimestamp(
                series["start"], timezone.utc).isoformat())
            payload["matchTeams"] = [{"teamId": "team-" + team, "code": "team-" + team,
                                      "result": {"gameWins": wins}}
                                     for team, wins in zip(series["teams"], series["score"])]
            payload["match"]["strategy"]["count"] = max(series["score"]) * 2 - 1
            payloads.append(payload)
        parsed = parse(payloads)
        target = next(s for s in parsed if s.get("live_title") == "Worlds Swiss Stage 2-1")
        self.assertEqual(worlds.announcement(target, game_start=3).title, "Worlds · Live game")
        self.assertIn("Worlds Swiss Stage 2-1 · Game 3", worlds.announcement(target, game_start=3).description)
        target = dict(target, state="inProgress", score=[0, 0])
        self.assertEqual(worlds.announcement(target).title, "Worlds · Series started")
        self.assertIn("Worlds Swiss · 2-1 Bracket", worlds.announcement(target).description)
        self.assertNotIn("Game 1", worlds.announcement(target).description)
        self.assertEqual(worlds.announcement(target).color.value, 0x2ECC71)
        self.assertTrue(worlds.announcement(target).description.startswith("**"))
        self.assertIn("Best of", worlds.announcement(target).description)
        self.assertIsNone(worlds.announcement(target, game_start=3).url)
        self.assertIn(worlds.WATCH_URL, worlds.announcement(target, game_start=3).description)
        self.assertIn("result", worlds.announcement(target, score=True).title)

    def test_filters_other_leagues_and_rejects_inconsistent_scores(self):
        with self.assertRaises(ValueError):
            parse(event(league="lck"))
        bad = event((1, 0), "inProgress")
        bad["match"]["games"] = []
        with self.assertRaises(ValueError):
            parse(bad)
        with self.assertRaises(ValueError):
            parse(event((1, 1), "completed"))

    def test_real_schema_and_final_embed(self):
        series = parse(event((2, 1), "completed"))[0]
        embed = worlds.announcement(series, final=True)
        self.assertIn("AAA 2–1 BBB", embed.description)
        self.assertIn("AAA wins", embed.description)
        self.assertIn("Final result", embed.title)

    def test_duplicate_renders_do_not_regress_score(self):
        series = parse([event((1, 0), "inProgress"), event()])
        self.assertEqual(series[0]["score"], [1, 0])

    def test_live_query_wins_over_stale_schedule_with_same_score(self):
        series = parse([event(), event(state="inProgress")])
        self.assertEqual(series[0]["state"], "inProgress")


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.deleted = AsyncMock()
        self.channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=100)),
                                       get_partial_message=Mock(return_value=SimpleNamespace(delete=self.deleted)))
        self.bot = SimpleNamespace(get_channel=lambda _: self.channel)
        self.feed = parse(event())
        self.now = datetime(2026, 10, 28, 21, tzinfo=timezone.utc)
        patches = [patch.object(worlds, "STATE_PATH", Path(self.temp.name) / "worlds.json"),
                   patch("bot_app.worlds_schedule.series_subscribers", new=AsyncMock(return_value=[])),
                   patch.object(worlds, "resolve_worlds_channels", new=AsyncMock(return_value={123})),
                   patch.object(worlds, "fetch_series", side_effect=lambda: self.feed)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def poll(self, score=(0, 0), state="unstarted"):
        self.feed = parse(event(score, state))
        await worlds.poll_worlds(self.bot, now=self.now)

    async def test_start_and_score_only_game_results_with_restart_deduplication(self):
        await self.poll()
        await self.poll(state="inProgress")
        await self.poll(state="inProgress")
        await self.poll((1, 0), "inProgress")
        await self.poll((1, 0), "inProgress")
        await self.poll((1, 1), "inProgress")
        await self.poll((2, 1), "completed")
        await self.poll((2, 1), "completed")
        self.assertEqual(self.channel.send.await_count, 4)
        for call in self.channel.send.call_args_list:
            self.assertNotIn("view", call.kwargs)
            self.assertEqual(len(call.kwargs["embed"].fields), 0)
        titles = [call.kwargs["embed"].title for call in self.channel.send.call_args_list]
        self.assertEqual(titles, ["Worlds · Series started", "Worlds · Game result",
                                  "Worlds · Game result", "Worlds · Final result"])
        self.assertIn("1–0", self.channel.send.call_args_list[1].kwargs["embed"].description)

    async def test_no_historical_results_on_install(self):
        await self.poll((2, 0), "completed")
        self.channel.send.assert_not_awaited()

    async def test_retry_failure_without_repeating_start(self):
        await self.poll()
        await self.poll(state="inProgress")
        self.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "no access")
        with self.assertLogs(worlds.LOGGER, level="ERROR"):
            await self.poll((2, 0), "completed")
        self.channel.send.side_effect = None
        await self.poll((2, 0), "completed")
        self.assertEqual(self.channel.send.await_count, 3)
        self.assertEqual(self.channel.send.call_args.kwargs["embed"].title, "Worlds · Final result")

    async def test_missed_games_use_verified_latest_score_and_ignore_regression(self):
        await self.poll()
        await self.poll((1, 1), "inProgress")
        self.assertEqual(self.channel.send.await_count, 2)
        await self.poll((1, 0), "inProgress")
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_failed_game_score_retries_without_duplicate_start(self):
        await self.poll(state="inProgress")
        self.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "blocked")
        with self.assertLogs(worlds.LOGGER, level="ERROR"):
            await self.poll((1, 0), "inProgress")
        saved = json.loads(worlds.STATE_PATH.read_text())["channels"]["123"]["series"]["series-1"]
        self.assertEqual(saved["score"], [0, 0])
        self.channel.send.side_effect = None
        await self.poll((1, 0), "inProgress")
        await self.poll((1, 0), "inProgress")
        self.assertEqual(self.channel.send.await_count, 3)
        self.assertEqual(self.channel.send.call_args.kwargs["embed"].title, "Worlds · Game result")

    async def test_feed_failure_preserves_receipts(self):
        await self.poll()
        saved = worlds.STATE_PATH.read_text()
        with patch.object(worlds, "fetch_series", side_effect=ValueError("schema changed")):
            with self.assertRaises(ValueError):
                await worlds.poll_worlds(self.bot, now=self.now)
        self.assertEqual(worlds.STATE_PATH.read_text(), saved)

    async def test_reversed_live_team_order_announces_and_keeps_scores_attributed(self):
        await self.poll()
        for score in ((0, 0), (1, 0)):
            payload = event(score, "inProgress")
            payload["matchTeams"].reverse()
            self.feed = parse(payload)
            await worlds.poll_worlds(self.bot, now=self.now)
        self.assertEqual(self.channel.send.await_count, 2)
        saved = json.loads(worlds.STATE_PATH.read_text())["channels"]["123"]["series"]["series-1"]
        self.assertEqual(saved["score"], [1, 0])

    async def test_automatic_results_never_fetch_or_upgrade_stats(self):
        await self.poll()
        with patch("bot_app.worlds_stats.publish_game_stats", new=AsyncMock()) as publish, \
             patch("bot_app.worlds_stats_views.upgrade_stats_messages", new=AsyncMock()) as upgrade:
            await self.poll((1, 0), "inProgress")
            await self.poll((2, 0), "completed")
            await self.poll((2, 0), "completed")
        publish.assert_not_awaited()
        upgrade.assert_not_awaited()
        self.assertEqual(self.channel.send.await_count, 3)
        self.assertEqual(self.channel.send.call_args.kwargs["embed"].title, "Worlds · Final result")
        self.assertNotIn("view", self.channel.send.call_args.kwargs)

    def active_game(self, number, score):
        payload = event(score, "inProgress")
        payload["match"]["games"].append({"id": str(number * 111), "number": number, "state": "inProgress"})
        return parse(payload)

    async def test_later_game_starts_stay_silent_across_polls_and_saved_receipts(self):
        self.feed = self.active_game(1, (0, 0))
        await worlds.poll_worlds(self.bot, now=self.now)
        self.feed = self.active_game(2, (1, 0))
        await worlds.poll_worlds(self.bot, now=self.now)
        await worlds.poll_worlds(self.bot, now=self.now)
        titles = [call.kwargs["embed"].title for call in self.channel.send.call_args_list]
        self.assertEqual(titles, ["Worlds · Series started", "Worlds · Game result"])
        self.assertEqual(self.channel.send.call_args.kwargs["allowed_mentions"].to_dict()["parse"], [])
        saved = json.loads(worlds.STATE_PATH.read_text())["channels"]["123"]["series"]["series-1"]
        self.assertNotIn("game_starts", saved)

    async def test_failed_series_start_retries_once_then_later_starts_are_silent(self):
        self.feed = self.active_game(1, (0, 0))
        self.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "blocked")
        with self.assertLogs(worlds.LOGGER, level="ERROR"):
            await worlds.poll_worlds(self.bot, now=self.now)
        self.channel.send.side_effect = None
        await worlds.poll_worlds(self.bot, now=self.now)
        await self.poll((1, 0), "inProgress")
        self.feed = self.active_game(2, (1, 0))
        await worlds.poll_worlds(self.bot, now=self.now)
        await worlds.poll_worlds(self.bot, now=self.now)
        self.assertEqual(self.channel.send.await_count, 3)
        self.assertEqual(self.channel.send.call_args.kwargs["embed"].title, "Worlds · Game result")

    async def test_first_seen_during_game_two_posts_start_and_current_score(self):
        self.feed = self.active_game(2, (1, 0))
        await worlds.poll_worlds(self.bot, now=self.now)
        titles = [call.kwargs["embed"].title for call in self.channel.send.call_args_list]
        self.assertEqual(titles.count("Worlds · Series started"), 1)
        self.assertEqual(len(titles), 2)  # Latest cumulative score is delivered once.

    async def test_finished_games_do_not_get_historical_start_alerts(self):
        payload = event((2, 0), "completed")
        payload["match"]["games"].append({"id": "222", "number": 2, "state": "inProgress"})
        self.feed = parse(payload)
        self.assertEqual(self.feed[0]["active_games"], [])
        await worlds.poll_worlds(self.bot, now=self.now)
        self.channel.send.assert_not_awaited()

    async def test_series_notice_deleted_after_final_and_cleanup_retries_without_reposting(self):
        self.channel.send.side_effect = [SimpleNamespace(id=n) for n in (101, 102, 103)]
        self.feed = self.active_game(3, (1, 1))
        await worlds.poll_worlds(self.bot, now=self.now)
        self.deleted.assert_not_awaited()
        failure = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "blocked")
        self.deleted.side_effect = [failure, None, None]
        with self.assertLogs(worlds.LOGGER, level="ERROR"):
            await self.poll((2, 1), "completed")
        self.assertEqual(self.channel.send.await_count, 3)
        saved = json.loads(worlds.STATE_PATH.read_text())["channels"]["123"]["series"]["series-1"]
        self.assertTrue(saved["final"])
        self.assertEqual(saved["live_message_ids"], [101, 102])
        await self.poll((2, 1), "completed")
        await self.poll((2, 1), "completed")
        self.assertEqual(self.channel.send.await_count, 3)
        self.assertEqual(self.deleted.await_count, 3)
        self.assertEqual([call.args for call in self.channel.get_partial_message.call_args_list],
                         [(101,), (101,), (102,)])
        self.assertEqual(json.loads(worlds.STATE_PATH.read_text())["channels"]["123"]
                         ["series"]["series-1"]["live_message_ids"], [])

"""Final-frame validation, accurate team damage shares, and retryable result enrichment."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image

from bot_app import worlds_stats as stats
from bot_app.charts import build_worlds_damage_share_chart
from bot_app.worlds import announcement


def fixture():
    series = {"id": "456", "teams": ["a", "b"], "names": ["AAA", "BBB"],
              "team_names": ["Alpha", "Beta"], "score": [1, 0], "stage": "Swiss", "best_of": 3,
              "start": 1793210000, "games": [{"id": "123", "number": 1}]}
    frame = {"rfc460Timestamp": "2026-10-28T20:51:59.000Z", "gameState": "finished"}
    window = {"esportsGameId": "123", "esportsMatchId": "456", "gameMetadata": {}, "frames": [frame]}
    detail = {"rfc460Timestamp": frame["rfc460Timestamp"], "participants": []}
    for side, team_id, offset in (("blue", "a", 0), ("red", "b", 5)):
        metadata, counters = [], []
        for i, share in enumerate((.1, .2, .3, .25, .15), 1):
            pid = i + offset
            metadata.append({"participantId": pid, "summonerName": f"Player {pid}", "championId": "Ahri"})
            player = {"participantId": pid, "kills": i, "deaths": 2, "assists": 3}
            counters.append(player)
            detail["participants"].append(dict(player, championDamageShare=share))
        window["gameMetadata"][side + "TeamMetadata"] = {"esportsTeamId": team_id, "participantMetadata": metadata}
        frame[side + "Team"] = {"participants": counters}
    return series, series["games"][0], window, {"frames": [detail]}


class FinalStatsTests(unittest.TestCase):
    def test_requires_finished_frame_and_matching_final_damage_timestamp(self):
        series, game, window, details = fixture()
        result = stats.parse_final_stats(window, details, series, game)
        self.assertEqual(result["teams"][0]["players"][0]["kda"], [1, 2, 3])
        self.assertEqual(result["teams"][0]["players"][0]["damage_share"], .1)
        window["frames"][0]["gameState"] = "in_game"
        with self.assertRaises(ValueError):
            stats.parse_final_stats(window, details, series, game)
        window["frames"][0]["gameState"] = "finished"
        details["frames"][0]["rfc460Timestamp"] = "2026-10-28T20:51:58.000Z"
        with self.assertRaises(ValueError):
            stats.parse_final_stats(window, details, series, game)

    def test_rejects_wrong_teams_bad_damage_and_unsynchronized_kda(self):
        for invalid in ("team", "damage", "kda"):
            series, game, window, details = fixture()
            if invalid == "team":
                window["gameMetadata"]["blueTeamMetadata"]["esportsTeamId"] = "other"
            elif invalid == "damage":
                details["frames"][0]["participants"][0]["championDamageShare"] = float("nan")
            else:
                details["frames"][0]["participants"][0]["kills"] = 99
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                stats.parse_final_stats(window, details, series, game)

    def test_cache_contains_only_verified_final_snapshot(self):
        series, game, window, details = fixture()
        now = datetime(2026, 10, 28, 21, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder, patch.object(stats, "CACHE_DIR", Path(folder)), \
             patch.object(stats, "fetch_game_stats", return_value=(window, details)) as fetch:
            first = stats.final_stats(series, game, now)
            self.assertEqual(stats.final_stats(series, game, now), first)
            fetch.assert_called_once_with("123", "2026-10-28T20:59:30.000Z")

    def test_embed_and_chart_use_percent_of_own_team(self):
        series, game, window, details = fixture()
        data = stats.parse_final_stats(window, details, series, game)
        embed = stats.add_stats_fields(announcement(series, score=True), data, series)
        self.assertEqual(len(embed.fields), 6)
        self.assertEqual(embed.fields[2].value.splitlines()[0], "10.0%")
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        chart = build_worlds_damage_share_chart(data, series)
        with Image.open(chart.fp) as image:
            self.assertEqual(image.format, "PNG")
            image.verify()


class StatsPublishingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.series, self.game, window, details = fixture()
        self.series["state"] = "completed"
        self.now = datetime(2026, 10, 28, 21, tzinfo=timezone.utc)
        self.data = stats.parse_final_stats(window, details, self.series, self.game)
        self.record = {"final": True, "message_id": 99, "series": self.series, "status": "pending", "created": self.now.timestamp()}
        self.receipt = {"started": True, "score": [1, 0], "stats_games": {"123": self.record}}
        self.message = SimpleNamespace(id=99, edit=AsyncMock(), embeds=[announcement(self.series, score=True)])
        self.channel = SimpleNamespace(id=10, fetch_message=AsyncMock(return_value=self.message),
                                       send=AsyncMock(return_value=self.message))
        self.save = AsyncMock()
        p = patch.object(stats, "build_worlds_damage_share_chart", side_effect=lambda *_: discord.File(BytesIO(b"png"), filename="stats.png"))
        p.start()
        self.addCleanup(p.stop)

    async def test_updates_existing_score_message_without_pings_or_duplicate_send(self):
        with patch.object(stats, "final_stats", return_value=self.data):
            await stats.publish_game_stats(self.channel, self.series, self.receipt, self.now, self.save)
        self.channel.send.assert_not_awaited()
        self.message.edit.assert_awaited_once()
        options = self.message.edit.call_args.kwargs
        self.assertEqual(len(options["embed"].fields), 6)
        self.assertEqual(options["embed"].image.url, "attachment://stats.png")
        self.assertFalse(options["file"].spoiler)
        self.assertEqual(options["allowed_mentions"].to_dict()["parse"], [])
        self.assertEqual(self.record["status"], "sent")
        self.assertFalse(stats.stats_due(self.series, self.receipt, self.now))

    async def test_missing_final_stats_retry_without_repeating_score(self):
        with patch.object(stats, "final_stats", side_effect=ValueError("not final")):
            await stats.publish_game_stats(self.channel, self.series, self.receipt, self.now, self.save)
        self.channel.send.assert_not_awaited()
        self.message.edit.assert_not_awaited()
        self.assertEqual(self.receipt["score"], [1, 0])
        self.assertFalse(stats.stats_due(self.series, self.receipt, self.now))
        later = self.now + timedelta(seconds=60)
        self.assertTrue(stats.stats_due(self.series, self.receipt, later))
        with patch.object(stats, "final_stats", return_value=self.data):
            await stats.publish_game_stats(self.channel, self.series, self.receipt, later, self.save)
        self.message.edit.assert_awaited_once()

    async def test_seeded_historical_matches_do_not_backfill_stats(self):
        self.receipt["historical"] = True
        self.assertFalse(stats.stats_due(self.series, self.receipt, self.now))

    async def test_missing_attachment_permission_still_delivers_player_stats(self):
        self.channel.guild = SimpleNamespace(me=object())
        self.channel.permissions_for = lambda _: SimpleNamespace(attach_files=False)
        with patch.object(stats, "final_stats", return_value=self.data):
            await stats.publish_game_stats(self.channel, self.series, self.receipt, self.now, self.save)
        options = self.message.edit.call_args.kwargs
        self.assertEqual(len(options["embed"].fields), 6)
        self.assertNotIn("file", options)
        self.assertIn("Chart unavailable", options["embed"].footer.text)
        self.assertEqual(self.record["status"], "sent")

    async def test_games_without_final_result_message_are_cached_without_supplement(self):
        self.receipt["stats_games"] = {}
        with patch.object(stats, "final_stats", return_value=self.data):
            await stats.publish_game_stats(self.channel, self.series, self.receipt, self.now, self.save)
            await stats.publish_game_stats(self.channel, self.series, self.receipt, self.now, self.save)
        self.channel.send.assert_not_awaited()
        self.message.edit.assert_not_awaited()
        self.assertEqual(self.receipt["stats_games"]["123"]["status"], "cached")

    async def test_intermediate_score_message_never_gets_stats_or_charts(self):
        self.record["final"] = False
        self.series["state"] = "inProgress"
        with patch.object(stats, "final_stats", return_value=self.data), \
             patch.object(stats, "build_worlds_damage_share_chart") as chart:
            await stats.publish_game_stats(self.channel, self.series, self.receipt, self.now, self.save)
        self.channel.send.assert_not_awaited()
        self.message.edit.assert_not_awaited()
        chart.assert_not_called()
        self.assertEqual(self.record["status"], "cached")

"""Tests for champion matchup win-rate aggregation."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.counterstats import aggregate
from bot_app.commands.counterstats import CounterStatsCommands, CounterStatsView, _counter_embed
from bot_app.counterstats import CounterRecord, CounterStatsReport


def _match(match_id, *, win, role="TOP", enemy="Ahri", enemy_role="TOP", queue_id=420):
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "queueId": queue_id,
            "participants": [
                {"participantId": 1, "puuid": "me", "championName": "Garen", "teamId": 100, "teamPosition": role, "win": win},
                {"participantId": 6, "puuid": f"enemy-{match_id}", "championName": enemy, "teamId": 200, "teamPosition": enemy_role, "win": not win},
                {"puuid": f"ally-{match_id}", "championName": "Lux", "teamId": 100, "win": win},
            ],
        },
    }


def _timeline(my_gold, enemy_gold):
    return {
        "info": {
            "frames": [{
                "timestamp": 15 * 60_000,
                "participantFrames": {
                    "1": {"totalGold": my_gold, "xp": 0, "minionsKilled": 0, "jungleMinionsKilled": 0},
                    "6": {"totalGold": enemy_gold, "xp": 0, "minionsKilled": 0, "jungleMinionsKilled": 0},
                },
                "events": [],
            }]
        }
    }


class CounterStatsTests(unittest.TestCase):
    def test_counts_each_enemy_once_per_game_and_sorts_by_win_rate(self):
        report = aggregate(
            [_match("1", win=True, enemy="Ahri"), _match("2", win=False, enemy="Ahri"), _match("3", win=True, enemy="Zed")],
            "me",
            "Garen",
        )
        self.assertEqual((report.games, report.wins), (3, 2))
        self.assertEqual([(row.champion, row.games, row.wins) for row in report.counters], [("Zed", 1, 1), ("Ahri", 2, 1)])

    def test_usage_filter_requires_more_than_one_percent(self):
        payloads = [_match(str(index), win=True, enemy=f"Enemy{index}") for index in range(99)]
        payloads.extend([_match("99", win=False, enemy="Zed"), _match("100", win=True, enemy="Zed")])
        report = aggregate(payloads, "me", "Garen", min_usage_rate=0.01)
        self.assertEqual([row.champion for row in report.counters], ["Zed"])

    def test_role_filter_applies_to_the_enemy_team(self):
        report = aggregate(
            [_match("1", win=True, role="TOP", enemy_role="TOP"), _match("2", win=False, role="TOP", enemy_role="JUNGLE"), _match("3", win=True, queue_id=450, enemy_role="JUNGLE")],
            "me",
            "Garen",
            role="Top",
            enemy_role="Jungle",
        )
        self.assertEqual((report.games, report.wins, report.role, report.enemy_role), (2, 1, "Top", "Jungle"))

    def test_duplicate_match_payload_is_ignored(self):
        report = aggregate([_match("1", win=True), _match("1", win=True)], "me", "Garen")
        self.assertEqual((report.games, report.counters[0].games), (1, 1))

    def test_laning_mode_uses_15_minute_gold_instead_of_final_result(self):
        won_game_lost_lane = _match("1", win=True)
        lost_game_won_lane = _match("2", win=False)
        report = aggregate(
            [won_game_lost_lane, lost_game_won_lane], "me", "Garen",
            timelines={"1": _timeline(4_000, 5_000), "2": _timeline(6_000, 5_000)},
            laning=True,
        )
        self.assertEqual((report.games, report.wins, report.laning), (2, 1, True))
        self.assertEqual((report.counters[0].games, report.counters[0].wins), (2, 1))

    def test_laning_mode_excludes_ties_and_missing_timelines(self):
        report = aggregate(
            [_match("1", win=True), _match("2", win=True)], "me", "Garen",
            timelines={"1": _timeline(5_000, 5_000)},
            laning=True,
        )
        self.assertEqual((report.games, report.wins, report.counters), (0, 0, ()))

    def test_embed_pages_keep_every_field_under_discord_limit(self):
        report = CounterStatsReport(
            "Garen", "All Games", "All Roles", 100, 50,
            tuple(CounterRecord(f"Enemy {index}", 1, index % 2) for index in range(100)),
        )
        for page in range(7):
            embed = _counter_embed(report, "player", page)
            self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))

    def test_role_filter_is_reflected_in_report(self):
        report = aggregate([_match("1", win=True, role="TOP", enemy_role="JUNGLE")], "me", "Garen", role="Top", enemy_role="Jungle")
        self.assertEqual((report.role, report.enemy_role), ("Top", "Jungle"))

    def test_controls_never_expire(self):
        async def build_view() -> CounterStatsView:
            return CounterStatsView([], "me", "Garen", "All Games", "Top", False, "player", 1)

        view = asyncio.run(build_view())

        self.assertIsNone(view.timeout)

    def test_background_scan_edits_channel_message(self):
        async def run():
            view = CounterStatsView([], "me", "Garen", "All Games", "Top", False, "player", 1)
            message = Mock(id=123)
            message.channel.get_partial_message.return_value.edit = AsyncMock()
            with patch("bot_app.commands.counterstats._load_payloads", return_value=[_match("1", win=True)]):
                await CounterStatsCommands(Mock())._finish_scan(message, view, "me", "NA1")
            message.channel.get_partial_message.assert_called_once_with(123)
            message.channel.get_partial_message.return_value.edit.assert_awaited_once()
            message.edit.assert_not_called()

        asyncio.run(run())

    def test_background_scan_ignores_deleted_message(self):
        async def run():
            view = CounterStatsView([], "me", "Garen", "All Games", "Top", False, "player", 1)
            message = Mock(id=123)
            message.channel.get_partial_message.return_value.edit = AsyncMock(
                side_effect=discord.NotFound(Mock(status=404, reason="Not Found"), "Unknown Message")
            )
            with patch("bot_app.commands.counterstats._load_payloads", return_value=[_match("1", win=True)]), \
                    patch("bot_app.commands.counterstats.LOGGER") as logger:
                await CounterStatsCommands(Mock())._finish_scan(message, view, "me", "NA1")
            logger.exception.assert_not_called()
            logger.info.assert_called_once()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

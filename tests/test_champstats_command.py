"""Tests for the public ``/champstats`` presentation controls."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from bot_app.champstats import ChampionStatsReport, ChoiceRecord
from bot_app.commands.champstats import ChampStatsView, _filtered, _load_report


class ChampStatsFilterTests(unittest.TestCase):
    """Verify the optional one-percent and minimum-pick filter."""

    def test_filter_keeps_exactly_one_percent_and_removes_lower_rates(self) -> None:
        records = (
            ChoiceRecord(1, "Exactly One", 3, 1),
            ChoiceRecord(2, "Below One", 0, 0),
            ChoiceRecord(3, "Two Percent", 6, 1),
        )

        filtered = _filtered(records, 300, True)

        self.assertEqual(tuple(record.name for record in filtered), ("Exactly One", "Two Percent"))

    def test_filter_removes_choices_with_two_or_fewer_picks(self) -> None:
        records = (
            ChoiceRecord(1, "Two Picks", 2, 2),
            ChoiceRecord(2, "Three Picks", 3, 2),
        )

        filtered = _filtered(records, 100, True)

        self.assertEqual(tuple(record.name for record in filtered), ("Three Picks",))

    def test_filter_can_be_disabled(self) -> None:
        record = ChoiceRecord(1, "Rare", 1, 0)

        self.assertEqual(_filtered((record,), 100, False), (record,))

    def test_filter_keeps_no_boots_stats_visible(self) -> None:
        record = ChoiceRecord(0, "No Boots", 1, 0)

        self.assertEqual(_filtered((record,), 100, True), (record,))

    def test_controls_never_expire(self) -> None:
        async def build_view() -> ChampStatsView:
            return ChampStatsView(
                ChampionStatsReport("Garen", "All Games", 0, 0, (), (), (), ()),
                "player",
                1,
            )

        view = asyncio.run(build_view())

        self.assertIsNone(view.timeout)

    def test_returning_to_runes_syncs_the_select_and_resets_page(self) -> None:
        """Switching back to Runes leaves the component state aligned with the embed."""
        async def exercise() -> None:
            report = ChampionStatsReport(
                "Garen", "All Games", 20, 10,
                (ChoiceRecord(1, "Conqueror", 10, 5),),
                (ChoiceRecord(2, "Triumph", 10, 5),),
                tuple(ChoiceRecord(index, f"Item {index}", 1, 1) for index in range(12)),
                tuple(ChoiceRecord(index, f"Boot {index}", 1, 1) for index in range(12)),
            )
            view = ChampStatsView(report, "player", 1)
            view.selected = "Boots"
            view.page = 1
            view._refresh_buttons()

            view.selected = "Runes"
            view._refresh_buttons()

            self.assertEqual(view.page, 0)
            self.assertEqual(
                [option.value for option in view._select.options if option.default],
                ["Runes"],
            )

        asyncio.run(exercise())


class ChampStatsLoadingTests(unittest.TestCase):
    """Keep timeline recovery from scanning unrelated account history."""

    def test_timeline_fetches_only_eligible_requested_champion_games(self) -> None:
        def payload(match_id: str, champion: str, queue_id: int = 420) -> dict:
            return {
                "metadata": {"matchId": match_id},
                "info": {
                    "queueId": queue_id,
                    "gameDuration": 1200,
                    "participants": [{
                        "puuid": "player", "participantId": 1,
                        "championName": champion, "teamPosition": "BOTTOM",
                        "item0": 3031, "win": True,
                    }],
                },
            }

        cache = MagicMock()
        cache.champion_match_payloads.return_value = [payload("jinx", "Jinx")]
        cache.get_timeline.return_value = None
        client = MagicMock()
        client.match_ids.return_value = []
        client.match_timeline.return_value = {"info": {"frames": []}}

        with (
            patch("bot_app.commands.champstats.MatchCache", return_value=cache),
            patch("bot_app.commands.champstats.get_client", return_value=client),
            patch("bot_app.commands.champstats.ddragon.item_metadata", return_value={}),
        ):
            _load_report("player", "NA1", "Jinx", "Ranked Solo/Duo", role="ADC")

        client.match_timeline.assert_called_once_with("jinx", "NA1")

    def test_timeline_skips_other_champions_and_duplicate_matches(self) -> None:
        def payload(match_id: str, champion: str) -> dict:
            return {
                "metadata": {"matchId": match_id},
                "info": {
                    "queueId": 420, "gameDuration": 1200,
                    "participants": [{
                        "puuid": "player", "participantId": 1,
                        "championName": champion, "teamPosition": "BOTTOM",
                        "item0": 3031, "win": True,
                    }],
                },
            }

        jinx = payload("jinx", "Jinx")
        cache = MagicMock()
        cache.champion_match_payloads.return_value = [jinx]
        cache.get_timeline.return_value = None
        client = MagicMock()
        client.match_ids.side_effect = [["jinx", "ashe"], []]
        client.match.side_effect = [jinx, payload("ashe", "Ashe")]
        client.match_timeline.return_value = {"info": {"frames": []}}

        with (
            patch("bot_app.commands.champstats.MatchCache", return_value=cache),
            patch("bot_app.commands.champstats.get_client", return_value=client),
            patch("bot_app.commands.champstats.ddragon.item_metadata", return_value={}),
        ):
            _load_report("player", "NA1", "Jinx", "All Games", role="ADC")

        client.match_timeline.assert_called_once_with("jinx", "NA1")

if __name__ == "__main__":
    unittest.main()

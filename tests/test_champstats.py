"""Tests for champion-history aggregation."""

from __future__ import annotations

import unittest

from bot_app.champstats import aggregate
from bot_app.ddragon import ItemMetadata


class ChampionStatsAggregationTests(unittest.TestCase):
    """Verify final inventory choices, including ADC boot upgrades."""

    def test_patch_filter_accepts_major_minor_version(self) -> None:
        """Only matches from the requested patch contribute to the report."""
        def payload(match_id: str, version: str, win: bool) -> dict:
            return {
                "metadata": {"matchId": match_id},
                "info": {
                    "queueId": 420,
                    "gameVersion": version,
                    "gameDuration": 901,
                    "participants": [{
                        "puuid": "player",
                        "championName": "Jinx",
                        "teamPosition": "BOTTOM",
                        "win": win,
                    }],
                },
            }

        report = aggregate(
            [payload("old", "15.24.1", True), payload("new", "16.1.1", False)],
            "player",
            "Jinx",
            item_metadata={},
            patch="16.1",
        )

        self.assertEqual(report.games, 1)
        self.assertEqual(report.wins, 0)
        self.assertEqual(report.patch, "16.1")

    def test_oldest_match_date_uses_only_included_matches(self) -> None:
        """The report records the oldest eligible match timestamp."""
        payload = {
            "metadata": {"matchId": "included"},
            "info": {
                "queueId": 420,
                "gameDuration": 901,
                "gameEndTimestamp": 1_700_000_000_000,
                "participants": [{
                    "puuid": "player", "championName": "Jinx",
                    "teamPosition": "BOTTOM", "win": True,
                }],
            },
        }

        report = aggregate([payload], "player", "Jinx", item_metadata={})

        self.assertEqual(report.earliest_match_timestamp, 1_700_000_000_000)

    def test_adc_boot_name_is_classified_when_catalog_tag_is_missing(self) -> None:
        payload = {
            "metadata": {"matchId": "NA1_1"},
            "info": {
                "queueId": 420,
                "gameDuration": 901,
                "participants": [{
                    "puuid": "player",
                    "championName": "Jinx",
                    "teamPosition": "BOTTOM",
                    "win": True,
                    "item0": 3031,
                    "item1": 9999,
                    "item2": 0,
                    "item3": 0,
                    "item4": 0,
                    "item5": 0,
                }],
            },
        }
        metadata = {
            3031: ItemMetadata(3031, "Infinity Edge", ("Damage",), depth=3),
            9999: ItemMetadata(9999, "Test Greaves", (), depth=3),
        }

        report = aggregate(payloads=[payload], puuid="player", champion="Jinx", item_metadata=metadata, role="ADC")

        self.assertEqual(report.games, 1)
        self.assertEqual(tuple(record.name for record in report.boots), ("Test Greaves",))
        self.assertEqual(len(report.items), 1)
        self.assertEqual(report.items[0].name, "Infinity Edge")

    def test_final_item_survives_nonstandard_data_dragon_depth(self) -> None:
        """A catalog depth change must not hide a real final-slot item."""
        payload = {
            "metadata": {"matchId": "NA1_DEPTH"},
            "info": {
                "queueId": 420,
                "gameDuration": 901,
                "participants": [{
                    "puuid": "player", "championName": "Jinx",
                    "teamPosition": "BOTTOM", "win": True,
                    "item0": 3031,
                }],
            },
        }
        metadata = {
            3031: ItemMetadata(3031, "Infinity Edge", ("Damage",), from_ids=(1038, 1018), depth=2),
        }

        report = aggregate([payload], "player", "Jinx", item_metadata=metadata)

        self.assertEqual(tuple(record.identifier for record in report.items), (3031,))

    def test_intermediate_component_is_not_a_final_item(self) -> None:
        """An item with an upgrade path stays out of Final Items."""
        payload = {
            "metadata": {"matchId": "NA1_COMPONENT"},
            "info": {
                "queueId": 420,
                "gameDuration": 901,
                "participants": [{
                    "puuid": "player", "championName": "Jinx",
                    "teamPosition": "BOTTOM", "win": True,
                    "item0": 1038,
                }],
            },
        }
        metadata = {
            1038: ItemMetadata(1038, "B. F. Sword", ("Damage",), depth=2, into_ids=(3031,)),
        }

        report = aggregate([payload], "player", "Jinx", item_metadata=metadata)

        self.assertEqual(report.items, ())

    def test_adc_recovers_boot_omitted_from_final_slots_from_timeline(self) -> None:
        """ADC boots replaced after purchase are recovered for /champstats."""
        payload = {
            "metadata": {"matchId": "NA1_TIMELINE"},
            "info": {
                "queueId": 420,
                "gameDuration": 901,
                "participants": [{
                    "puuid": "player",
                    "participantId": 9,
                    "championName": "Jinx",
                    "teamPosition": "BOTTOM",
                    "win": True,
                    "item0": 3031,
                    "item1": 6672,
                    "item2": 3036,
                    "item3": 3085,
                    "item4": 1038,
                    "item5": 1037,
                }],
            },
        }
        timeline = {
            "info": {"frames": [{"events": [
                {"type": "ITEM_PURCHASED", "participantId": 9, "itemId": 3008, "timestamp": 100},
            ]}]}
        }

        report = aggregate(
            [payload], "player", "Jinx", item_metadata={}, role="ADC",
            timelines={"NA1_TIMELINE": timeline},
        )

        self.assertEqual(tuple(record.identifier for record in report.boots), (3008,))

    def test_only_long_matches_count_and_no_boots_has_a_win_rate(self) -> None:
        """Short games are excluded and qualifying no-boot games are counted."""
        def payload(match_id: str, duration: int, win: bool) -> dict:
            return {
                "metadata": {"matchId": match_id},
                "info": {
                    "queueId": 420,
                    "gameDuration": duration,
                    "participants": [{
                        "puuid": "player", "championName": "Jinx",
                        "teamPosition": "BOTTOM", "win": win,
                        "item0": 3031, "item1": 3036, "item2": 3085,
                        "item3": 1038, "item4": 1037, "item5": 0,
                    }],
                },
            }

        report = aggregate(
            [payload("short", 900, True), payload("long", 901, False)],
            "player", "Jinx", item_metadata={}, role="ADC",
            timelines={"long": {"info": {"frames": []}}},
        )

        self.assertEqual(report.games, 1)
        self.assertEqual(report.boots[0].name, "No Boots")
        self.assertEqual(report.boots[0].win_rate, 0.0)

    def test_no_boots_is_available_for_all_roles(self) -> None:
        """Final inventories without boots contribute for every role."""
        payload = {
            "metadata": {"matchId": "TOP_NO_BOOT"},
            "info": {
                "queueId": 420, "gameDuration": 901,
                "participants": [{
                    "puuid": "player", "championName": "Jinx",
                    "teamPosition": "TOP", "win": True,
                    "item0": 3031, "item1": 3036,
                }],
            },
        }

        report = aggregate(
            [payload], "player", "Jinx", item_metadata={},
            timelines={"TOP_NO_BOOT": {"info": {"frames": []}}},
        )

        self.assertEqual(report.boots[0].name, "No Boots")


if __name__ == "__main__":
    unittest.main()

"""The flattened rank scale, and snapshot serialisation."""

import unittest

from bot_app.queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID
from bot_app.ranks import (
    APEX_THRESHOLD,
    RankSnapshot,
    lp_change,
    rank_queue_for_match,
    rank_queue_label,
    rank_value,
    value_to_rank,
)


class RankValueTests(unittest.TestCase):
    def test_scale_is_monotonic_across_divisions_and_tiers(self) -> None:
        ladder = [
            ("IRON", "IV", 0),
            ("IRON", "I", 99),
            ("BRONZE", "IV", 0),
            ("DIAMOND", "I", 99),
            ("MASTER", "", 0),
            ("CHALLENGER", "", 800),
        ]
        values = [rank_value(*rung) for rung in ladder]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(value is not None for value in values))

    def test_apex_tiers_share_one_lp_pool(self) -> None:
        self.assertEqual(rank_value("MASTER", "", 300), rank_value("CHALLENGER", "", 300))
        self.assertEqual(rank_value("MASTER", "", 0), APEX_THRESHOLD)

    def test_unranked_and_unknown_tiers_have_no_value(self) -> None:
        self.assertIsNone(rank_value(None, "", 0))
        self.assertIsNone(rank_value("MYTHIC", "I", 50))

    def test_value_round_trips_below_master(self) -> None:
        for tier, division, lp in [("GOLD", "II", 47), ("EMERALD", "IV", 0), ("IRON", "I", 99)]:
            with self.subTest(tier=tier, division=division):
                self.assertEqual(value_to_rank(rank_value(tier, division, lp)), (tier, division, lp))

    def test_value_above_the_apex_threshold_reports_flat_lp(self) -> None:
        self.assertEqual(value_to_rank(rank_value("GRANDMASTER", "", 412)), ("MASTER", "", 412))

    def test_value_to_rank_of_none_is_none(self) -> None:
        self.assertIsNone(value_to_rank(None))


class SnapshotTests(unittest.TestCase):
    def test_unranked_snapshot_is_not_ranked(self) -> None:
        snapshot = RankSnapshot()
        self.assertFalse(snapshot.is_ranked)
        self.assertIsNone(snapshot.value)
        self.assertIsNone(snapshot.winrate)

    def test_winrate(self) -> None:
        self.assertAlmostEqual(RankSnapshot("GOLD", "I", 0, 60, 40).winrate, 0.6)

    def test_state_round_trip_keeps_the_legacy_field_names(self) -> None:
        snapshot = RankSnapshot("PLATINUM", "II", 34, 10, 8)
        state = snapshot.to_state()
        self.assertEqual(set(state), {"tier", "rank", "lp", "wins", "losses"})
        self.assertEqual(RankSnapshot.from_state(state), snapshot)

    def test_from_state_rejects_non_dicts(self) -> None:
        self.assertIsNone(RankSnapshot.from_state(None))
        self.assertIsNone(RankSnapshot.from_state(["not", "a", "dict"]))


class LpChangeTests(unittest.TestCase):
    def test_gain_and_loss_are_signed(self) -> None:
        gold_two = RankSnapshot("GOLD", "II", 40)
        gold_one = RankSnapshot("GOLD", "I", 8)
        self.assertEqual(lp_change(gold_two, gold_one), "+68 LP")
        self.assertEqual(lp_change(gold_one, gold_two), "-68 LP")

    def test_promotion_across_tiers_is_measured_on_one_scale(self) -> None:
        self.assertEqual(
            lp_change(RankSnapshot("GOLD", "I", 88), RankSnapshot("PLATINUM", "IV", 10)),
            "+22 LP",
        )

    def test_missing_or_unranked_sides_produce_no_label(self) -> None:
        self.assertIsNone(lp_change(None, RankSnapshot("GOLD", "I", 8)))
        self.assertIsNone(lp_change(RankSnapshot(), RankSnapshot("GOLD", "I", 8)))


class RankQueueTests(unittest.TestCase):
    def test_flex_games_use_flex_standing(self) -> None:
        self.assertEqual(rank_queue_for_match(FLEX_QUEUE_ID), FLEX_QUEUE_ID)

    def test_every_other_queue_falls_back_to_solo(self) -> None:
        for queue_id in (SOLO_QUEUE_ID, 400, 450, 1700, None):
            with self.subTest(queue_id=queue_id):
                self.assertEqual(rank_queue_for_match(queue_id), SOLO_QUEUE_ID)

    def test_label_names_the_queue_being_shown(self) -> None:
        self.assertEqual(rank_queue_label(FLEX_QUEUE_ID), "Flex Rank:")
        self.assertEqual(rank_queue_label(SOLO_QUEUE_ID), "Solo/Duo Rank:")


if __name__ == "__main__":
    unittest.main()

"""LP history schema, arithmetic, retention, and timezone windows."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from bot_app.lp_history import since_local_midnight, summarize, summary_text
from bot_app.queues import SOLO_QUEUE_ID
from bot_app.ranks import RankSnapshot
from bot_app.store import PlayerState


class LpHistoryTests(unittest.TestCase):
    def test_summary_omits_unattributed_count(self) -> None:
        """Keep resync bookkeeping out of the user-facing LP summary."""
        summary = summarize([{"t": 1, "d": None}])
        self.assertNotIn("Unattributed games/resyncs", summary_text(summary))

    def test_promotion_delta_and_resync(self) -> None:
        """Verify that promotion delta and resync."""
        state = PlayerState(ranks={SOLO_QUEUE_ID: RankSnapshot("GOLD", "I", 90, 10, 8)})
        state.record_rank(
            SOLO_QUEUE_ID,
            RankSnapshot("PLATINUM", "IV", 12, 11, 8),
            match_id="m1",
            won=True,
            timestamp=100,
        )
        state.record_rank(
            SOLO_QUEUE_ID,
            RankSnapshot("PLATINUM", "IV", 30, 12, 9),
            match_id="m2",
            won=None,
            attributable=False,
            timestamp=200,
        )
        self.assertEqual(state.history[SOLO_QUEUE_ID][0]["d"], 22)
        self.assertIsNone(state.history[SOLO_QUEUE_ID][1]["d"])

    def test_history_caps_at_500_and_legacy_round_trip(self) -> None:
        """Verify that history caps at 500 and legacy round trip."""
        state = PlayerState.from_json({"matches": ["m"], "solo": None, "flex": None})
        for index in range(510):
            state.record_rank(
                SOLO_QUEUE_ID,
                RankSnapshot("IRON", "IV", index),
                match_id=str(index),
                won=True,
                timestamp=index,
            )
        loaded = PlayerState.from_json(state.to_json())
        self.assertEqual(len(loaded.history[SOLO_QUEUE_ID]), 500)
        self.assertEqual(loaded.history[SOLO_QUEUE_ID][0]["m"], "10")

    def test_season_reset_has_null_delta(self) -> None:
        """Verify that season reset has null delta."""
        state = PlayerState(
            ranks={SOLO_QUEUE_ID: RankSnapshot("DIAMOND", "I", 50, 100, 80)}
        )
        state.record_rank(
            SOLO_QUEUE_ID,
            RankSnapshot("EMERALD", "IV", 0, 1, 0),
            match_id="placement",
            won=True,
        )
        self.assertIsNone(state.history[SOLO_QUEUE_ID][-1]["d"])

    def test_local_midnight_boundary(self) -> None:
        """Verify that local midnight boundary."""
        zone = ZoneInfo("America/Los_Angeles")
        now = datetime(2026, 8, 14, 12, tzinfo=zone)
        midnight = now.replace(hour=0).timestamp()
        state = PlayerState(
            history={
                SOLO_QUEUE_ID: [
                    {"t": midnight - 1, "d": 99},
                    {"t": midnight, "d": 12, "w": True},
                ]
            }
        )
        entries = since_local_midnight(
            state, SOLO_QUEUE_ID, "America/Los_Angeles", now=now
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(summarize(entries).net, 12)

"""Poller state: match-id retention and the legacy on-disk formats."""

import unittest

from bot_app.ranks import RankSnapshot
from bot_app.store import (
    PlayerState,
    dedupe_tail,
    load_live_game_state,
    save_live_game_state,
)


class DedupeTailTests(unittest.TestCase):
    def test_keeps_the_newest_ids_in_order(self) -> None:
        """Verify that keeps the newest ids in order."""
        ids = [f"NA1_{index}" for index in range(150)]
        kept = dedupe_tail(ids, limit=100)
        self.assertEqual(kept, ids[-100:])

    def test_removes_duplicates_but_keeps_first_position(self) -> None:
        """Verify that removes duplicates but keeps first position."""
        self.assertEqual(dedupe_tail(["a", "b", "a", "c"]), ["a", "b", "c"])

    def test_empty_input(self) -> None:
        """Verify that empty input."""
        self.assertEqual(dedupe_tail([]), [])


class PlayerStateTests(unittest.TestCase):
    def test_reads_the_legacy_bare_list_format(self) -> None:
        """Verify that reads the legacy bare list format."""
        state = PlayerState.from_json(["NA1_1", "NA1_2"])
        self.assertEqual(state.matches, ["NA1_1", "NA1_2"])
        self.assertEqual(state.ranks, {})

    def test_reads_the_current_format(self) -> None:
        """Verify that reads the current format."""
        state = PlayerState.from_json(
            {
                "matches": ["NA1_1"],
                "solo": {
                    "tier": "GOLD",
                    "rank": "II",
                    "lp": 40,
                    "wins": 5,
                    "losses": 4,
                },
                "flex": None,
            }
        )
        self.assertEqual(state.matches, ["NA1_1"])
        self.assertEqual(state.ranks[420], RankSnapshot("GOLD", "II", 40, 5, 4))
        self.assertIsNone(state.ranks[440])

    def test_round_trips_through_json(self) -> None:
        """Verify that round trips through json."""
        state = PlayerState(
            matches=["NA1_1"], ranks={420: RankSnapshot("IRON", "IV", 1)}
        )
        self.assertEqual(PlayerState.from_json(state.to_json()).matches, state.matches)
        self.assertEqual(
            PlayerState.from_json(state.to_json()).ranks[420],
            RankSnapshot("IRON", "IV", 1),
        )

    def test_remember_appends_and_caps(self) -> None:
        """Verify that remember appends and caps."""
        state = PlayerState(matches=["NA1_1"])
        state.remember(["NA1_1", "NA1_2"])
        self.assertEqual(state.matches, ["NA1_1", "NA1_2"])

    def test_malformed_entries_degrade_to_empty(self) -> None:
        """Verify that malformed entries degrade to empty."""
        self.assertEqual(PlayerState.from_json("nonsense").matches, [])


class LiveGameStateTests(unittest.TestCase):
    def test_state_round_trip(self) -> None:
        """Verify that state round trip."""
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from pathlib import Path

        with (
            TemporaryDirectory() as directory,
            patch("bot_app.store.LIVE_GAME_STATE_PATH", Path(directory) / "live.json"),
        ):
            save_live_game_state({"123": "NA1:456"})
            self.assertEqual(load_live_game_state(), {"123": "NA1:456"})


if __name__ == "__main__":
    unittest.main()

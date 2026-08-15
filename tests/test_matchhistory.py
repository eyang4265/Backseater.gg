"""Compact history-line formatting."""

import unittest

from bot_app.history import format_match_history_line


class MatchHistoryLineTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare fixtures for the test case."""
        self.info = {"queueId": 420, "gameDuration": 1_505}
        self.participant = {
            "win": True,
            "kills": 8,
            "deaths": 2,
            "assists": 6,
            "totalMinionsKilled": 180,
            "neutralMinionsKilled": 12,
        }

    def test_includes_the_players_result_and_game_summary(self) -> None:
        """Verify that includes the players result and game summary."""
        self.assertEqual(
            format_match_history_line(
                self.info,
                self.participant,
                champion_label="Wukong",
                time_label="3 hours ago",
            ),
            "🟦 **Wukong** — Victory\n"
            "Ranked Solo/Duo · 8/2/6 · 192 CS · 25:05 · 3 hours ago",
        )

    def test_a_loss_uses_a_red_result_marker(self) -> None:
        """Verify that a loss uses a red result marker."""
        self.participant["win"] = False
        self.assertTrue(
            format_match_history_line(
                self.info, self.participant, champion_label="Wukong", time_label="now"
            ).startswith("🟥 **Wukong** — Defeat")
        )


if __name__ == "__main__":
    unittest.main()

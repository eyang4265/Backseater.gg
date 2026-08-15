"""Stored leaderboard ordering."""

import unittest

from bot_app.leaderboard import leaderboard_rows
from bot_app.queues import SOLO_QUEUE_ID
from bot_app.ranks import RankSnapshot
from bot_app.store import Account, PlayerState


class LeaderboardTests(unittest.TestCase):
    def test_orders_rank_then_games_with_unranked_last(self) -> None:
        """Verify that orders rank then games with unranked last."""
        accounts = {
            "1": Account("1", "p1", "NA1", "Gold#1"),
            "2": Account("2", "p2", "NA1", "Plat#2"),
            "3": Account("3", "p3", "NA1", "None#3"),
        }
        state = {
            "1": PlayerState(
                ranks={SOLO_QUEUE_ID: RankSnapshot("GOLD", "I", 99, 50, 50)}
            ),
            "2": PlayerState(
                ranks={SOLO_QUEUE_ID: RankSnapshot("PLATINUM", "IV", 0, 4, 3)}
            ),
            "3": PlayerState(ranks={SOLO_QUEUE_ID: RankSnapshot()}),
        }
        self.assertEqual(
            [
                row.account.discord_id
                for row in leaderboard_rows(accounts, state, SOLO_QUEUE_ID)
            ],
            ["2", "1", "3"],
        )

    def test_empty_roster(self) -> None:
        """Verify that empty roster."""
        self.assertEqual(leaderboard_rows({}, {}, SOLO_QUEUE_ID), [])

    def test_read_only_refresh_overrides_display_rank(self) -> None:
        """Verify that read only refresh overrides display rank."""
        accounts = {"1": Account("1", "p1", "NA1", "Player#NA1")}
        baseline = RankSnapshot("GOLD", "II", 20)
        fresh = RankSnapshot("GOLD", "I", 10)
        state = {"1": PlayerState(ranks={SOLO_QUEUE_ID: baseline})}
        rows = leaderboard_rows(
            accounts,
            state,
            SOLO_QUEUE_ID,
            rank_overrides={"1": {SOLO_QUEUE_ID: fresh}},
        )
        self.assertEqual(rows[0].rank, fresh)
        self.assertEqual(state["1"].ranks[SOLO_QUEUE_ID], baseline)

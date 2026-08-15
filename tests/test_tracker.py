"""Regression tests for finished-match and live-game polling."""

import unittest
from unittest.mock import Mock, patch

from bot_app.announce import MatchAnnouncement
from bot_app.queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID
from bot_app.ranks import RankSnapshot
from bot_app.store import Account, PlayerState
from bot_app.tracker import (
    _AccountPoll,
    collect_new_live_games,
    collect_new_matches,
    update_all_rank_snapshots,
)


def _match(match_id: str, *, finished: bool = True, queue_id: int = SOLO_QUEUE_ID):
    """Handle match."""
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "queueId": queue_id,
            "gameEndTimestamp": 1_000 if finished else None,
            "participants": [{"puuid": "p1", "win": True}],
        },
    }


class MatchPollTests(unittest.TestCase):
    def test_ranked_flex_match_is_announced(self) -> None:
        """Verify that Ranked Flex matches use the ranked announcement path."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = PlayerState(ranks={FLEX_QUEUE_ID: RankSnapshot("GOLD", "II", 20)})
        poll = _AccountPoll(account, state, ["NA1_FLEX_1"])
        announcement = MatchAnnouncement("flex", "Victory", _match("NA1_FLEX_1", queue_id=FLEX_QUEUE_ID), {"p1"})

        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tracker_state", return_value={"1": state}),
            patch("bot_app.tracker._poll_accounts", return_value=[poll]),
            patch(
                "bot_app.tracker._fetch_matches",
                return_value={"NA1_FLEX_1": _match("NA1_FLEX_1", queue_id=FLEX_QUEUE_ID)},
            ),
            patch("bot_app.tracker.fetch_ranks", return_value={FLEX_QUEUE_ID: RankSnapshot("GOLD", "I", 10)}),
            patch("bot_app.tracker.format_match", return_value=announcement),
            patch("bot_app.tracker.save_tracker_state"),
        ):
            announcements = collect_new_matches()

        self.assertEqual(announcements, [announcement])
        self.assertEqual(state.matches, ["NA1_FLEX_1"])

    def test_startup_refresh_does_not_overwrite_an_existing_baseline(self) -> None:
        """Verify that startup refresh does not overwrite an existing baseline."""
        before = RankSnapshot("GOLD", "II", 20, 5, 4)
        after = RankSnapshot("GOLD", "II", 40, 6, 4)
        state = {"1": PlayerState(ranks={SOLO_QUEUE_ID: before})}
        with (
            patch("bot_app.tracker.load_tracker_state", return_value=state),
            patch(
                "bot_app.tracker.fetch_all_rank_snapshots",
                return_value=({"1": {SOLO_QUEUE_ID: after}}, 0),
            ),
            patch("bot_app.tracker.save_tracker_state") as save,
        ):
            self.assertEqual(update_all_rank_snapshots(), (1, 0))
        self.assertEqual(state["1"].ranks[SOLO_QUEUE_ID], before)
        save.assert_not_called()

    def test_back_to_back_games_are_announced_and_resync_rank(self) -> None:
        """Verify that back to back games are announced and resync rank."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = PlayerState(ranks={SOLO_QUEUE_ID: RankSnapshot("GOLD", "II", 20, 5, 4)})
        poll = _AccountPoll(account, state, ["NA1_1", "NA1_2"])
        saved = {}

        def formatted(match, players):
            """Handle formatted."""
            self.assertEqual(len(players), 1)
            return MatchAnnouncement("ok", "Victory", match, {"p1"})

        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tracker_state", return_value={"1": state}),
            patch("bot_app.tracker._poll_accounts", return_value=[poll]),
            patch(
                "bot_app.tracker._fetch_matches",
                return_value={"NA1_1": _match("NA1_1"), "NA1_2": _match("NA1_2")},
            ),
            patch(
                "bot_app.tracker.fetch_ranks",
                return_value={SOLO_QUEUE_ID: RankSnapshot("GOLD", "I", 10, 7, 4)},
            ),
            patch("bot_app.tracker.format_match", side_effect=formatted),
            patch(
                "bot_app.tracker.save_tracker_state",
                side_effect=lambda value: saved.update(value),
            ),
        ):
            announcements = collect_new_matches()

        self.assertEqual(len(announcements), 2)
        self.assertEqual(state.ranks[SOLO_QUEUE_ID].division, "I")
        self.assertIsNone(state.history[SOLO_QUEUE_ID][-1]["d"])
        self.assertEqual(state.matches, ["NA1_1", "NA1_2"])
        self.assertIn("1", saved)

    def test_unfinished_match_is_retried_without_moving_baseline(self) -> None:
        """Verify that unfinished match is retried without moving baseline."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        before = RankSnapshot("GOLD", "II", 20, 5, 4)
        state = PlayerState(ranks={SOLO_QUEUE_ID: before})
        poll = _AccountPoll(account, state, ["NA1_1"])
        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tracker_state", return_value={"1": state}),
            patch("bot_app.tracker._poll_accounts", return_value=[poll]),
            patch(
                "bot_app.tracker._fetch_matches",
                return_value={"NA1_1": _match("NA1_1", finished=False)},
            ),
            patch(
                "bot_app.tracker.fetch_ranks",
                return_value={SOLO_QUEUE_ID: RankSnapshot("GOLD", "II", 40)},
            ),
            patch("bot_app.tracker.format_match", return_value=None),
            patch("bot_app.tracker.save_tracker_state"),
        ):
            self.assertEqual(collect_new_matches(), [])
        self.assertEqual(state.matches, [])
        self.assertEqual(state.ranks[SOLO_QUEUE_ID], before)

    def test_finished_malformed_match_does_not_duplicate_history(self) -> None:
        """Verify that finished malformed match does not duplicate history."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        before = RankSnapshot("GOLD", "II", 20, 5, 4)
        state = PlayerState(ranks={SOLO_QUEUE_ID: before})
        malformed = _match("NA1_1")
        malformed["info"]["participants"] = [{"puuid": "someone-else", "win": True}]

        def run_once():
            """Handle once."""
            poll = _AccountPoll(account, state, ["NA1_1"])
            with (
                patch("bot_app.tracker.load_accounts", return_value={"1": account}),
                patch("bot_app.tracker.load_tracker_state", return_value={"1": state}),
                patch("bot_app.tracker._poll_accounts", return_value=[poll]),
                patch(
                    "bot_app.tracker._fetch_matches", return_value={"NA1_1": malformed}
                ),
                patch("bot_app.tracker.fetch_ranks") as fetch,
                patch("bot_app.tracker.save_tracker_state"),
                patch("bot_app.announce.ddragon.catalog", return_value=None),
            ):
                self.assertEqual(collect_new_matches(), [])
                fetch.assert_not_called()

        run_once()
        run_once()
        self.assertEqual(state.history.get(SOLO_QUEUE_ID, []), [])
        self.assertEqual(state.ranks[SOLO_QUEUE_ID], before)


class LivePollTests(unittest.TestCase):
    def test_finished_game_seen_on_startup_is_not_announced_as_live(self) -> None:
        """Verify that a completed game discovered after downtime skips live post."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        client = Mock()
        client.active_game.return_value = {
            "gameId": 123,
            "participants": [{"puuid": "p1"}],
        }
        client.match.return_value = _match("NA1_123")
        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_live_game_state", return_value={}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.save_live_game_state"),
            patch("bot_app.tracker.format_live_game") as format_live,
        ):
            self.assertEqual(collect_new_live_games(), [])
        format_live.assert_not_called()

    def test_removed_accounts_are_pruned_from_live_state(self) -> None:
        """Verify that removed accounts are pruned from live state."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        client = Mock()
        client.active_game.return_value = None
        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch(
                "bot_app.tracker.load_live_game_state",
                return_value={"1": "NA1:1", "gone": "NA1:2"},
            ),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.save_live_game_state") as save,
        ):
            self.assertEqual(collect_new_live_games(), [])
        save.assert_called_once_with({"1": "NA1:1"})

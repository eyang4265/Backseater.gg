"""Guest announcements require Guest and CrispyPineapple in the same game."""

import unittest
from unittest.mock import Mock, patch

from bot_app.guest_policy import CRISPY_PUUID, GUEST_PUUID
from bot_app.guest_tracker import build_guest_players, collect_new_guest_matches
from bot_app.queues import SOLO_QUEUE_ID
from bot_app.store import Account, PlayerState
from bot_app.tracker import _AccountPoll, collect_new_live_games, collect_new_matches


def _participants(
    *, same_team: bool, include_guest: bool = True, include_crispy: bool = True
) -> list[dict]:
    """Handle participants."""
    participants = []
    if include_guest:
        participants.append(
            {"puuid": GUEST_PUUID, "riotIdGameName": "boba monkey ball", "teamId": 100}
        )
    if include_crispy:
        participants.append(
            {
                "puuid": CRISPY_PUUID,
                "riotIdGameName": "CrispyPineapple",
                "teamId": 100 if same_team else 200,
            }
        )
    return participants


def _match(
    *,
    same_team: bool,
    queue_id: int = SOLO_QUEUE_ID,
    include_crispy: bool = True,
) -> dict:
    """Handle match."""
    return {
        "metadata": {"matchId": "NA1_1"},
        "info": {
            "queueId": queue_id,
            "gameEndTimestamp": 1_000,
            "participants": _participants(
                same_team=same_team,
                include_crispy=include_crispy,
            ),
        },
    }


class GuestSelectionTests(unittest.TestCase):
    @patch("bot_app.guest_tracker.get_client")
    def test_special_match_requires_both_players_but_not_same_team(
        self, client: Mock
    ) -> None:
        """Verify that special match requires both players but not same team."""
        client.return_value.riot_id.return_value = "CrispyPineapple#NA1"
        self.assertIsNotNone(build_guest_players(_match(same_team=True)))
        self.assertIsNotNone(build_guest_players(_match(same_team=False)))
        self.assertIsNone(
            build_guest_players(_match(same_team=True, include_crispy=False))
        )

    def test_special_match_allows_non_ranked_games(self) -> None:
        """Verify that special match allows non ranked games."""
        client = Mock()
        client.match_ids.return_value = ["NA1_1"]
        client.match.return_value = _match(same_team=True, queue_id=400)
        client.riot_id.return_value = "CrispyPineapple#NA1"
        with (
            patch("bot_app.guest_tracker.get_client", return_value=client),
            patch("bot_app.guest_tracker.load_guest_matches", return_value=[]),
            patch("bot_app.guest_tracker.save_guest_matches"),
            patch(
                "bot_app.guest_tracker.format_match", return_value=Mock()
            ) as formatted,
        ):
            self.assertEqual(len(collect_new_guest_matches()), 1)
        self.assertFalse(formatted.call_args.kwargs["require_ranked_queue"])

    def test_normal_match_suppresses_guest_without_crispy(self) -> None:
        """Verify that normal match suppresses guest without crispy."""
        guest = Account("guest", GUEST_PUUID, "NA1", "Guest")
        state = PlayerState()
        poll = _AccountPoll(guest, state, ["NA1_1"])
        with (
            patch("bot_app.tracker.load_accounts", return_value={"guest": guest}),
            patch("bot_app.tracker.load_tracker_state", return_value={"guest": state}),
            patch("bot_app.tracker._poll_accounts", return_value=[poll]),
            patch(
                "bot_app.tracker._fetch_matches",
                return_value={"NA1_1": _match(same_team=False, include_crispy=False)},
            ),
            patch("bot_app.tracker.fetch_ranks", return_value={}),
            patch("bot_app.tracker.format_match", return_value=None) as format_match,
            patch("bot_app.tracker.save_tracker_state"),
        ):
            self.assertEqual(collect_new_matches(), [])
        format_match.assert_called_once_with(
            _match(same_team=False, include_crispy=False), []
        )
        self.assertEqual(state.matches, ["NA1_1"])

    def test_normal_match_includes_guest_when_crispy_is_on_other_team(self) -> None:
        """Verify that normal match includes guest when crispy is on other team."""
        guest = Account("guest", GUEST_PUUID, "NA1", "Guest")
        state = PlayerState()
        poll = _AccountPoll(guest, state, ["NA1_1"])
        with (
            patch("bot_app.tracker.load_accounts", return_value={"guest": guest}),
            patch("bot_app.tracker.load_tracker_state", return_value={"guest": state}),
            patch("bot_app.tracker._poll_accounts", return_value=[poll]),
            patch(
                "bot_app.tracker._fetch_matches",
                return_value={"NA1_1": _match(same_team=False)},
            ),
            patch("bot_app.tracker.fetch_ranks", return_value={}),
            patch("bot_app.tracker.format_match") as format_match,
            patch("bot_app.tracker.save_tracker_state"),
        ):
            collect_new_matches()
        self.assertEqual(format_match.call_args.args[1][0].puuid, GUEST_PUUID)


class GuestLiveGameTests(unittest.TestCase):
    def _collect(
        self,
        *,
        same_team: bool,
        queue_id: int = SOLO_QUEUE_ID,
        account_puuid: str = GUEST_PUUID,
        include_guest: bool = True,
        include_crispy: bool = True,
    ):
        """Collect collect."""
        account = Account("account", account_puuid, "NA1", "Guest")
        client = Mock()
        client.active_game.return_value = {
            "gameId": 123,
            "gameQueueConfigId": queue_id,
            "participants": _participants(
                same_team=same_team,
                include_guest=include_guest,
                include_crispy=include_crispy,
            ),
        }
        with (
            patch("bot_app.tracker.load_accounts", return_value={"account": account}),
            patch("bot_app.tracker.load_live_game_state", return_value={}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.save_live_game_state"),
        ):
            return collect_new_live_games()

    def test_suppresses_guest_live_game_without_crispy(self) -> None:
        """Verify that suppresses guest live game without crispy."""
        self.assertEqual(self._collect(same_team=False, include_crispy=False), [])

    @patch("bot_app.tracker.format_live_game", return_value=Mock())
    def test_crispy_non_ranked_live_game_uses_normal_behavior(
        self, formatted: Mock
    ) -> None:
        """Verify that crispy non ranked live game uses normal behavior."""
        self.assertEqual(
            len(
                self._collect(
                    same_team=True,
                    queue_id=400,
                    account_puuid=CRISPY_PUUID,
                    include_guest=False,
                )
            ),
            1,
        )
        self.assertEqual(formatted.call_args.args[1][0].puuid, CRISPY_PUUID)

    @patch("bot_app.tracker.format_live_game", return_value=Mock())
    def test_includes_guest_non_ranked_live_game_with_crispy(
        self, formatted: Mock
    ) -> None:
        """Verify that includes guest non ranked live game with crispy."""
        self.assertEqual(len(self._collect(same_team=False, queue_id=400)), 1)
        self.assertEqual(formatted.call_args.args[1][0].puuid, GUEST_PUUID)


if __name__ == "__main__":
    unittest.main()

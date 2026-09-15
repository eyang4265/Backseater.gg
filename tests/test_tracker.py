"""Regression tests for finished-match and live-game polling."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot_app.announce import MatchAnnouncement
from bot_app.queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID
from bot_app.ranks import RankSnapshot
from bot_app.riot import RiotAPIError
from bot_app.store import Account, PlayerState, TftPlayerState
from bot_app.tracker import (
    _AccountPoll,
    _fetch_matches,
    _poll_accounts,
    collect_new_live_games,
    collect_new_matches,
    collect_new_tft_live_games,
    collect_new_tft_matches,
    update_all_rank_snapshots,
    update_all_tft_rank_snapshots,
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
    def test_match_fetch_failure_names_every_source_account(self) -> None:
        """A failed shared match lookup says whose histories surfaced its id."""
        first = Account("1", "p1", "NA1", "Player#NA1")
        second = Account("2", "p2", "NA1", "Friend#NA1")
        polls = [
            _AccountPoll(first, PlayerState(), ["NA1_5642521571"]),
            _AccountPoll(second, PlayerState(), ["NA1_5642521571"]),
        ]
        client = Mock()
        client.match.side_effect = RiotAPIError(
            "GET https://americas.api.riotgames.com/lol/match/v5/matches/NA1_5642521571 returned HTTP 404",
            status_code=404,
        )

        with patch("bot_app.tracker.get_client", return_value=client), self.assertLogs(
            "bot_app.tracker", level="WARNING"
        ) as logs:
            self.assertEqual(_fetch_matches(polls), {})

        self.assertIn(
            "Could not fetch match NA1_5642521571 for Player#NA1, Friend#NA1: GET https://americas.api.riotgames.com/",
            logs.output[0],
        )
        client.match.assert_called_once_with("NA1_5642521571", "NA1")

    def test_live_lobby_id_supplements_match_history_for_mayhem(self) -> None:
        """A Spectator game id surfaces modes omitted by the by-PUUID history."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = PlayerState()
        client = Mock()
        client.match_ids.return_value = []

        with (
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.load_live_game_state", return_value={"1": "NA1:123"}),
            patch("bot_app.tracker.load_live_game_messages", return_value={}),
        ):
            polls = _poll_accounts({"1": account}, {"1": state})

        self.assertEqual(polls[0].new_match_ids, ["NA1_123"])

    def test_live_lobby_candidate_is_only_attributed_to_mapped_accounts(self) -> None:
        """An active fallback id must not appear to originate from the whole roster."""
        playing = Account("1", "p1", "NA1", "Player#NA1")
        unrelated = Account("2", "p2", "NA1", "Other#NA1")
        client = Mock()
        client.match_ids.return_value = []

        with (
            patch("bot_app.tracker.get_client", return_value=client),
            patch(
                "bot_app.tracker.load_live_game_state",
                return_value={"1": "NA1:123"},
            ),
            patch("bot_app.tracker.load_live_game_messages", return_value={"NA1:123": []}),
        ):
            polls = _poll_accounts(
                {"1": playing, "2": unrelated},
                {"1": PlayerState(), "2": PlayerState()},
            )

        self.assertEqual(polls[0].new_match_ids, ["NA1_123"])
        self.assertEqual(polls[0].match_sources, {"NA1_123": "Player#NA1"})
        self.assertEqual(polls[0].expected_pending_match_ids, {"NA1_123"})
        self.assertEqual(polls[1].new_match_ids, [])

    def test_stale_live_announcement_is_labeled_without_inventing_players(self) -> None:
        """Message-only recovery has an honest source when its players are unknown."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        client = Mock()
        client.match_ids.return_value = []

        with (
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.load_live_game_state", return_value={}),
            patch("bot_app.tracker.load_live_game_messages", return_value={"NA1:123": []}),
        ):
            polls = _poll_accounts({"1": account}, {"1": PlayerState()})

        client.match.side_effect = RiotAPIError("HTTP 404", status_code=404)
        with patch("bot_app.tracker.get_client", return_value=client), self.assertLogs(
            "bot_app.tracker", level="DEBUG"
        ) as logs:
            _fetch_matches(polls)
        self.assertIn(
            "Match NA1_123 for saved live-game announcement is not published yet: HTTP 404",
            logs.output[0],
        )

    def test_normal_history_404_remains_a_warning(self) -> None:
        """A listed completed match disappearing from Match-V5 is unexpected."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        polls = [_AccountPoll(account, PlayerState(), ["NA1_123"])]
        client = Mock()
        client.match.side_effect = RiotAPIError("HTTP 404", status_code=404)

        with patch("bot_app.tracker.get_client", return_value=client), self.assertLogs(
            "bot_app.tracker", level="WARNING"
        ) as logs:
            _fetch_matches(polls)

        self.assertIn("Could not fetch match NA1_123 for Player#NA1", logs.output[0])

    def test_live_lobby_candidate_only_attributes_actual_participants(self) -> None:
        """A shared fallback candidate is not routed through unrelated accounts."""
        playing = Account("1", "p1", "NA1", "Player#NA1")
        unrelated = Account("2", "p2", "NA1", "Other#NA1")
        states = {"1": PlayerState(), "2": PlayerState()}
        match = _match("NA1_123", queue_id=2400)
        polls = [
            _AccountPoll(playing, states["1"], ["NA1_123"]),
            _AccountPoll(unrelated, states["2"], ["NA1_123"]),
        ]

        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": playing, "2": unrelated}),
            patch("bot_app.tracker.load_teammates", return_value={}),
            patch("bot_app.tracker.load_tracker_state", return_value=states),
            patch("bot_app.tracker._poll_accounts", return_value=polls),
            patch("bot_app.tracker._fetch_matches", return_value={"NA1_123": match}),
            patch("bot_app.tracker.save_tracker_state"),
            patch("bot_app.announce.ddragon.catalog", return_value=None),
        ):
            announcements = collect_new_matches()

        self.assertEqual(len(announcements), 1)
        self.assertEqual(announcements[0].highlight_puuids, {"p1"})

    def test_ranked_match_attributes_live_baseline_lp_change_to_teammate(self) -> None:
        """A teammate in a tracked lobby gets the LP delta captured at game start."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        teammate = Account("2", "mate", "NA1", "Mate#NA1")
        teammate_state = PlayerState(
            ranks={SOLO_QUEUE_ID: RankSnapshot("GOLD", "II", 20, 5, 4)}
        )
        state = {"1": PlayerState(), "teammate:mate": teammate_state}
        match = _match("NA1_1")
        match["info"]["participants"].append({"puuid": "mate", "win": True})
        poll = _AccountPoll(account, state["1"], ["NA1_1"])
        captured = []

        def formatted(match, players, **kwargs):
            captured.extend(players)
            return MatchAnnouncement("ok", "Victory", match, {p.puuid for p in players})

        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_teammates", return_value={"mate": teammate}),
            patch("bot_app.tracker.load_tracker_state", return_value=state),
            patch("bot_app.tracker._poll_accounts", return_value=[poll]),
            patch("bot_app.tracker._fetch_matches", return_value={"NA1_1": match}),
            patch(
                "bot_app.tracker.fetch_ranks",
                side_effect=[
                    {SOLO_QUEUE_ID: RankSnapshot("GOLD", "II", 35, 6, 4)},
                    {SOLO_QUEUE_ID: RankSnapshot("GOLD", "II", 38, 6, 4)},
                ],
            ),
            patch("bot_app.tracker.format_match", side_effect=formatted),
            patch("bot_app.tracker.save_tracker_state"),
        ):
            collect_new_matches()

        mate = next(player for player in captured if player.puuid == "mate")
        self.assertEqual(mate.lp_change, "+18 LP")
        self.assertEqual(teammate_state.ranks[SOLO_QUEUE_ID].lp, 38)

    def test_non_ranked_queues_announced_without_rank_changes(self) -> None:
        """Every non-ranked queue uses the real formatter without LP writes."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        for queue_id, expected_count, label in (
            (480, 1, "Swiftplay"),
            (710, 1, "Ranked 5s"),
            (400, 1, "Normal (Draft)"),
            (430, 1, "Normal (Blind)"),
            (450, 1, "ARAM"),
            (2400, 1, "ARAM: Mayhem"),
            (9999, 1, "Queue 9999"),
        ):
            with self.subTest(queue_id=queue_id):
                state = PlayerState()
                match = _match("NA1_1", queue_id=queue_id)
                poll = _AccountPoll(account, state, ["NA1_1"])
                with (
                    patch("bot_app.tracker.load_accounts", return_value={"1": account}),
                    patch("bot_app.tracker.load_tracker_state", return_value={"1": state}),
                    patch("bot_app.tracker._poll_accounts", return_value=[poll]),
                    patch("bot_app.tracker._fetch_matches", return_value={"NA1_1": match}),
                    patch("bot_app.tracker.fetch_ranks") as ranks,
                    patch("bot_app.tracker.save_tracker_state"),
                    patch("bot_app.announce.ddragon.catalog", return_value=None),
                ):
                    announcements = collect_new_matches()
                self.assertEqual(len(announcements), expected_count)
                if announcements:
                    self.assertIn(label, announcements[0].text)
                ranks.assert_not_called()
                self.assertEqual(state.ranks, {})
                self.assertEqual(state.matches, ["NA1_1"])

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

        def formatted(match, players, **kwargs):
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
    def test_ranked_live_lobby_saves_teammate_lp_baseline(self) -> None:
        """A teammate's starting LP is persisted when the shared lobby is announced."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        teammate = Account("2", "mate", "NA1", "Mate#NA1")
        game = {
            "gameId": 123,
            "gameQueueConfigId": SOLO_QUEUE_ID,
            "participants": [{"puuid": "p1"}, {"puuid": "mate"}],
        }
        client = Mock()
        client.active_game.return_value = game
        client.match.return_value = None
        saved = {}

        def formatted(game, players):
            self.assertEqual({player.puuid for player in players}, {"p1", "mate"})
            return Mock()

        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_teammates", return_value={"mate": teammate}),
            patch("bot_app.tracker.load_live_game_state", return_value={}),
            patch("bot_app.tracker.load_tracker_state", return_value={}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch(
                "bot_app.tracker.fetch_ranks",
                return_value={
                    SOLO_QUEUE_ID: RankSnapshot("GOLD", "II", 20, 5, 4)
                },
            ),
            patch("bot_app.tracker.save_live_game_state"),
            patch(
                "bot_app.tracker.save_tracker_state",
                side_effect=lambda value: saved.update(value),
            ),
            patch("bot_app.tracker.format_live_game", side_effect=formatted),
        ):
            self.assertEqual(len(collect_new_live_games()), 1)

        self.assertEqual(
            saved["teammate:mate"].ranks[SOLO_QUEUE_ID].lp,
            20,
        )

    def test_shared_new_lobby_is_verified_only_once(self) -> None:
        """Several tracked players in one lobby share one completion check."""
        accounts = {
            "1": Account("1", "p1", "NA1", "One#NA1"),
            "2": Account("2", "p2", "NA1", "Two#NA1"),
        }
        game = {
            "gameId": 123,
            "participants": [{"puuid": "p1"}, {"puuid": "p2"}],
        }
        client = Mock()
        client.active_game.return_value = game
        client.match.return_value = None
        announcement = Mock()
        with (
            patch("bot_app.tracker.load_accounts", return_value=accounts),
            patch("bot_app.tracker.load_live_game_state", return_value={}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.save_live_game_state"),
            patch("bot_app.tracker.format_live_game", return_value=announcement),
        ):
            self.assertEqual(collect_new_live_games(), [announcement])
        client.match.assert_called_once_with("NA1_123", "NA1")

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
        """Removed accounts and finished lobbies both leave the live state."""
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
        # "gone" is no longer tracked and "1" is confirmed out of any game.
        save.assert_called_once_with({})

    def test_poll_and_announce_deletes_the_live_post_then_announces(self) -> None:
        """A finished match's live post is deleted first, then the result posted."""
        from bot_app.tracker import poll_and_announce

        announced = MatchAnnouncement("t", "Victory", _match("EUW1_9"), {"p1"})
        calls: list[str] = []
        delete = AsyncMock(side_effect=lambda *a, **k: calls.append("delete"))
        publish = AsyncMock(side_effect=lambda *a, **k: calls.append("publish"))
        with (
            patch(
                "bot_app.tracker.resolve_announcement_channel",
                new=AsyncMock(return_value=Mock()),
            ),
            patch(
                "bot_app.tracker.collect_new_matches", return_value=[announced]
            ),
            patch("bot_app.tracker.collect_new_tft_matches", return_value=[]),
            patch("bot_app.tracker.publish", new=publish),
            patch("bot_app.tracker.delete_live_game_messages", new=delete),
        ):
            asyncio.run(poll_and_announce(Mock()))
        delete.assert_awaited_once()
        self.assertEqual(delete.await_args.args[1], ["EUW1:9"])
        self.assertEqual(calls, ["delete", "publish"])

    def test_stale_live_game_message_keys_are_those_missing_from_live_state(self) -> None:
        from bot_app.tracker import _stale_live_game_message_keys

        with (
            patch(
                "bot_app.tracker.load_live_game_state",
                return_value={"1": "NA1:1"},
            ),
            patch(
                "bot_app.tracker.load_live_game_messages",
                return_value={"NA1:1": [[1, 2]], "NA1:2": [[3, 4]]},
            ),
        ):
            self.assertEqual(_stale_live_game_message_keys(), ["NA1:2"])

    def test_transient_active_game_failure_keeps_the_live_key(self) -> None:
        """A failed active-game lookup must not retire a still-running lobby."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        client = Mock()
        client.active_game.side_effect = RiotAPIError("boom")
        with (
            patch("bot_app.tracker.load_accounts", return_value={"1": account}),
            patch(
                "bot_app.tracker.load_live_game_state",
                return_value={"1": "NA1:1"},
            ),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.save_live_game_state") as save,
        ):
            self.assertEqual(collect_new_live_games(), [])
        save.assert_not_called()


class TftPollTests(unittest.TestCase):
    def test_first_tft_poll_seeds_history_without_backfill(self) -> None:
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = TftPlayerState()
        client = Mock()
        client.tft_match_ids.return_value = ["NA1_2", "NA1_1"]
        with (
            patch("bot_app.tracker.load_tft_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tft_tracker_state", return_value={"1": state}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.save_tft_tracker_state") as save,
        ):
            self.assertEqual(collect_new_tft_matches(), [])
        self.assertEqual(state.matches, ["NA1_2", "NA1_1"])
        save.assert_called_once()
        client.tft_match.assert_not_called()

    def test_startup_seeds_missing_tft_rank_baseline(self) -> None:
        """A tracked TFT account with no baseline gets one seeded at startup."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = {"1": TftPlayerState(matches=["NA1_1"], initialized=True)}
        current = RankSnapshot("EMERALD", "III", 28, wins=30, losses=19)
        with (
            patch("bot_app.tracker.load_tft_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tft_tracker_state", return_value=state),
            patch("bot_app.tracker.fetch_tft_rank", return_value=current) as fetch_rank,
            patch("bot_app.tracker.save_tft_tracker_state") as save,
        ):
            self.assertEqual(update_all_tft_rank_snapshots(), (1, 0))
        fetch_rank.assert_called_once_with("p1", "NA1")
        self.assertEqual(state["1"].rank, current)
        save.assert_called_once()

    def test_startup_never_overwrites_an_existing_tft_baseline(self) -> None:
        """An account whose rank a real game already moved is left untouched."""
        account = Account("1", "p1", "NA1", "Player#NA1")
        before = RankSnapshot("EMERALD", "III", 28)
        state = {"1": TftPlayerState(matches=["NA1_1"], initialized=True, rank=before)}
        with (
            patch("bot_app.tracker.load_tft_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tft_tracker_state", return_value=state),
            patch(
                "bot_app.tracker.fetch_tft_rank",
                return_value=RankSnapshot("EMERALD", "II", 5),
            ),
            patch("bot_app.tracker.save_tft_tracker_state") as save,
        ):
            self.assertEqual(update_all_tft_rank_snapshots(), (0, 0))
        self.assertEqual(state["1"].rank, before)
        save.assert_not_called()

    def test_unseen_tft_match_uses_shared_formatter(self) -> None:
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = TftPlayerState(matches=["NA1_1"], initialized=True)
        match = {
            "info": {
                "game_datetime": 2,
                "participants": [{"puuid": "p1", "placement": 1}],
            }
        }
        announcement = MatchAnnouncement("tft", "Victory", match, {"p1"}, "tft")
        client = Mock()
        client.tft_match_ids.return_value = ["NA1_2", "NA1_1"]
        client.tft_match.return_value = match
        with (
            patch("bot_app.tracker.load_tft_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tft_tracker_state", return_value={"1": state}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.format_match", return_value=announcement) as formatter,
            patch("bot_app.tracker.save_tft_tracker_state"),
        ):
            self.assertEqual(collect_new_tft_matches(), [announcement])
        self.assertEqual(state.matches, ["NA1_1", "NA1_2"])
        self.assertEqual(formatter.call_args.kwargs["game_type"], "tft")

    def test_ranked_tft_match_attributes_lp_change_and_persists_rank(self) -> None:
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = TftPlayerState(
            matches=["NA1_1"],
            initialized=True,
            rank=RankSnapshot("GOLD", "IV", 50, wins=4, losses=3),
        )
        match = {
            "info": {
                "game_datetime": 2,
                "queue_id": 1100,
                "participants": [{"puuid": "p1", "placement": 2, "level": 9}],
            }
        }
        client = Mock()
        client.tft_match_ids.return_value = ["NA1_2", "NA1_1"]
        client.tft_match.return_value = match
        after = RankSnapshot("GOLD", "IV", 75, wins=5, losses=3)
        with (
            patch("bot_app.tracker.load_tft_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tft_tracker_state", return_value={"1": state}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.fetch_tft_rank", return_value=after) as fetch_rank,
            patch("bot_app.tracker.save_tft_tracker_state"),
        ):
            announcements = collect_new_tft_matches()
        fetch_rank.assert_called_once_with("p1", "NA1")
        self.assertIn("+25 LP", announcements[0].text)
        self.assertEqual(state.rank, after)

    def test_unranked_tft_match_never_looks_up_tft_rank(self) -> None:
        account = Account("1", "p1", "NA1", "Player#NA1")
        state = TftPlayerState(matches=["NA1_1"], initialized=True)
        match = {
            "info": {
                "game_datetime": 2,
                "queue_id": 1090,
                "participants": [{"puuid": "p1", "placement": 5, "level": 8}],
            }
        }
        client = Mock()
        client.tft_match_ids.return_value = ["NA1_2", "NA1_1"]
        client.tft_match.return_value = match
        with (
            patch("bot_app.tracker.load_tft_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tft_tracker_state", return_value={"1": state}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.fetch_tft_rank") as fetch_rank,
            patch("bot_app.tracker.save_tft_tracker_state"),
        ):
            collect_new_tft_matches()
        fetch_rank.assert_not_called()

    def test_tft_live_lobby_has_independent_dedupe_state(self) -> None:
        account = Account("1", "p1", "NA1", "Player#NA1")
        game = {"gameId": 123, "participants": [{"puuid": "p1"}]}
        announcement = Mock()
        client = Mock()
        client.tft_active_game.return_value = game
        client.tft_match.side_effect = RiotAPIError("still live", status_code=404)
        with (
            patch("bot_app.tracker.load_tft_accounts", return_value={"1": account}),
            patch("bot_app.tracker.load_tft_live_game_state", return_value={}),
            patch("bot_app.tracker.get_client", return_value=client),
            patch("bot_app.tracker.save_tft_live_game_state") as save,
            patch("bot_app.tracker.format_live_game", return_value=announcement) as formatter,
        ):
            self.assertEqual(collect_new_tft_live_games(), [announcement])
        save.assert_called_once_with({"1": "tft:NA1_123"})
        self.assertEqual(formatter.call_args.kwargs["game_type"], "tft")

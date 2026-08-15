"""Tracked-account registry persistence and seeding."""

import threading
import unittest
from unittest.mock import Mock, patch

from bot_app.account_registry import (
    DuplicateAccountError,
    RegistryError,
    track_account,
    untrack_account,
)
from bot_app.queues import SOLO_QUEUE_ID
from bot_app.ranks import RankSnapshot
from bot_app.store import (
    Account,
    load_accounts,
    load_live_game_state,
    load_tracker_state,
    save_accounts,
    save_live_game_state,
    update_accounts,
)
from tests.support import temporary_state


class RegistryTests(unittest.TestCase):
    def _client(self, puuid="p1"):
        """Handle client."""
        client = Mock()
        client.puuid.return_value = puuid
        client.riot_id.return_value = "Player#NA1"
        client.match_ids.return_value = ["NA1_2", "NA1_1"]
        return client

    def test_link_seeds_matches_and_rank_then_unlink_purges_state(self) -> None:
        """Verify that link seeds matches and rank then unlink purges state."""
        with (
            temporary_state(),
            patch("bot_app.account_registry.get_client", return_value=self._client()),
            patch(
                "bot_app.account_registry.fetch_ranks",
                return_value={SOLO_QUEUE_ID: RankSnapshot("GOLD", "IV", 20)},
            ),
        ):
            account = track_account("1", "Player", "NA1", "NA1")
            self.assertEqual(load_accounts()["1"], account)
            self.assertEqual(load_tracker_state()["1"].matches, ["NA1_2", "NA1_1"])
            save_live_game_state({"1": "NA1:game"})
            self.assertEqual(untrack_account("1"), account)
            self.assertEqual(load_accounts(), {})
            self.assertNotIn("1", load_tracker_state())
            self.assertEqual(load_live_game_state(), {})

    def test_duplicate_puuid_is_rejected(self) -> None:
        """Verify that duplicate puuid is rejected."""
        with (
            temporary_state(),
            patch("bot_app.account_registry.get_client", return_value=self._client()),
            patch("bot_app.account_registry.fetch_ranks", return_value={}),
        ):
            save_accounts({"2": Account("2", "p1", "NA1", "Other#NA1")})
            with self.assertRaises(DuplicateAccountError):
                track_account("1", "Player", "NA1", "NA1")

    def test_owner_reassign_purges_previous_live_state(self) -> None:
        """Verify that owner reassign purges previous live state."""
        with (
            temporary_state(),
            patch("bot_app.account_registry.get_client", return_value=self._client()),
            patch("bot_app.account_registry.fetch_ranks", return_value={}),
        ):
            save_accounts({"2": Account("2", "p1", "NA1", "Other#NA1")})
            save_live_game_state({"2": "NA1:old"})
            track_account("1", "Player", "NA1", "NA1", allow_reassign=True)
            self.assertEqual(load_live_game_state(), {})

    def test_duplicate_exception_preserves_its_message_in_args(self) -> None:
        """Verify that duplicate exception preserves its message in args."""
        error = DuplicateAccountError("123")
        self.assertEqual(
            error.args, ("That Riot account is already tracked by <@123>.",)
        )

    def test_roster_cap_rejects_a_new_account(self) -> None:
        """Verify that roster cap rejects a new account."""
        with (
            temporary_state(),
            patch("bot_app.account_registry.get_client", return_value=self._client()),
            patch("bot_app.account_registry.fetch_ranks", return_value={}),
        ):
            save_accounts({"2": Account("2", "p2", "NA1", "Other#NA1")})
            with self.assertRaises(RegistryError):
                track_account("1", "Player", "NA1", "NA1", max_accounts=1)

    def test_concurrent_updates_do_not_lose_an_account(self) -> None:
        """Verify that concurrent updates do not lose an account."""
        with temporary_state():
            barrier = threading.Barrier(2)

            def add(identifier):
                """Handle add."""
                barrier.wait()
                update_accounts(
                    lambda accounts: accounts.__setitem__(
                        identifier,
                        Account(
                            identifier, f"p{identifier}", "NA1", f"P{identifier}#NA1"
                        ),
                    )
                )

            threads = [
                threading.Thread(target=add, args=(identifier,))
                for identifier in ("1", "2")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(set(load_accounts()), {"1", "2"})

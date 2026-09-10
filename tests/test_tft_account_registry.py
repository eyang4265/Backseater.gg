"""Independent TFT account registration."""

import unittest
from unittest.mock import Mock, patch

from bot_app.store import Account, load_accounts, load_tft_accounts, load_tft_tracker_state
from bot_app.tft_account_registry import (
    track_tft_account,
    update_all_tft_from_league,
    update_tft_from_league,
)
from bot_app.account_registry import RegistryError
from bot_app.commands.add import _PartialRegistrationError, _track_both_accounts
from tests.support import temporary_state


class TftAccountRegistryTests(unittest.TestCase):
    def test_update_all_tft_continues_after_individual_failure(self) -> None:
        accounts = {
            "1": Account("1", "league-one", "NA1", "One#NA1"),
            "2": Account("2", "league-two", "EUW1", "Two#EUW"),
        }
        updated = (accounts["1"], Account("1", "tft-one", "NA1", "One#NA1"))
        with (
            patch(
                "bot_app.tft_account_registry.load_accounts",
                return_value=accounts,
            ),
            patch(
                "bot_app.tft_account_registry.update_tft_from_league",
                side_effect=[updated, RegistryError("TFT lookup failed")],
            ) as refresh,
        ):
            successes, failures = update_all_tft_from_league(max_accounts=12)

        self.assertEqual(successes, [updated])
        self.assertEqual(failures, {"2": "TFT lookup failed"})
        self.assertEqual(
            refresh.call_args_list,
            [
                unittest.mock.call("1", max_accounts=12),
                unittest.mock.call("2", max_accounts=12),
            ],
        )

    def test_update_tft_resolves_riot_id_from_stored_league_puuid(self) -> None:
        league = Account("1", "league-puuid", "EUW1", "Old Name#OLD")
        tft = Account("1", "tft-puuid", "EUW1", "New Name#NEW")
        client = Mock()
        client.riot_id.return_value = "New Name#NEW"
        with (
            patch(
                "bot_app.tft_account_registry.load_accounts",
                return_value={"1": league},
            ),
            patch("bot_app.tft_account_registry.get_client", return_value=client),
            patch(
                "bot_app.tft_account_registry.load_tft_accounts",
                return_value={"1": tft},
            ),
            patch(
                "bot_app.tft_account_registry.track_tft_account",
                return_value=tft,
            ) as track_tft,
        ):
            result = update_tft_from_league("1", max_accounts=12)

        self.assertEqual(result, (league, tft))
        client.riot_id.assert_called_once_with("league-puuid", "EUW1", refresh=True)
        track_tft.assert_called_once_with(
            "1",
            "New Name",
            "NEW",
            "EUW1",
            allow_reassign=True,
            max_accounts=12,
            known=tft,
        )

    def test_update_tft_requires_a_linked_league_account(self) -> None:
        with (
            patch("bot_app.tft_account_registry.load_accounts", return_value={}),
            self.assertRaisesRegex(RegistryError, "no tracked League account"),
        ):
            update_tft_from_league("1")

    def test_add_registers_both_unique_puuids(self) -> None:
        league = Mock(puuid="league-puuid", discord_id="1")
        tft = Mock(puuid="tft-puuid")
        with (
            patch("bot_app.commands.add.track_account", return_value=league) as add_league,
            patch("bot_app.commands.add.track_tft_account", return_value=tft) as add_tft,
        ):
            result = _track_both_accounts("1", "Player", "TAG", 25)
        self.assertEqual(result, (league, tft))
        self.assertNotEqual(result[0].puuid, result[1].puuid)
        self.assertEqual(add_league.call_args.args[:3], ("1", "Player", "TAG"))
        # The TFT half is keyed under whatever id track_account actually
        # settled, so an unlinked /add pairs both identities under one
        # synthetic sentinel rather than the raw (possibly None) input.
        self.assertEqual(add_tft.call_args.args[:3], ("1", "Player", "TAG"))

    def test_add_pairs_tft_under_the_settled_unlinked_key(self) -> None:
        league = Mock(puuid="league-puuid", discord_id="000000000000000002")
        with (
            patch("bot_app.commands.add.track_account", return_value=league),
            patch(
                "bot_app.commands.add.track_tft_account", return_value=Mock()
            ) as add_tft,
        ):
            _track_both_accounts(None, "Player", "TAG", 25)
        self.assertEqual(add_tft.call_args.args[0], "000000000000000002")

    def test_add_reports_when_only_league_succeeds(self) -> None:
        league = Mock(riot_id="League#TAG")
        with (
            patch("bot_app.commands.add.track_account", return_value=league),
            patch(
                "bot_app.commands.add.track_tft_account",
                side_effect=RegistryError("TFT failed"),
            ),
            self.assertRaises(_PartialRegistrationError) as raised,
        ):
            _track_both_accounts("1", "Player", "TAG", 25)
        self.assertIs(raised.exception.league_account, league)
        self.assertEqual(str(raised.exception), "TFT failed")

    def test_tft_account_uses_its_own_puuid_and_registry(self) -> None:
        client = Mock()
        client.tft_puuid.return_value = "tft-puuid"
        client.tft_home_platform.return_value = "NA1"
        client.tft_riot_id.return_value = "Tactician#TFT"
        client.tft_match_ids.return_value = ["NA1_TFT_1"]
        with temporary_state(), patch(
            "bot_app.tft_account_registry.get_client", return_value=client
        ):
            account = track_tft_account("1", "Tactician", "TFT", "NA1")

            self.assertEqual(account.puuid, "tft-puuid")
            self.assertEqual(load_tft_accounts()["1"], account)
            self.assertEqual(load_accounts(), {})
            state = load_tft_tracker_state()["1"]
            self.assertEqual(state.matches, ["NA1_TFT_1"])
            self.assertTrue(state.initialized)
        client.tft_puuid.assert_called()
        client.puuid.assert_not_called()

    def test_refreshing_a_known_account_skips_probe_and_reseed(self) -> None:
        """A whole-roster sweep must not re-pay for facts it already stored."""
        client = Mock()
        client.tft_puuid.return_value = "tft-puuid"
        client.tft_home_platform.return_value = "NA1"
        client.tft_riot_id.return_value = "Tactician#TFT"
        client.tft_match_ids.return_value = ["NA1_TFT_1"]
        with temporary_state(), patch(
            "bot_app.tft_account_registry.get_client", return_value=client
        ):
            first = track_tft_account("1", "Tactician", "TFT", "NA1")
            client.tft_home_platform.reset_mock()
            client.tft_match_ids.reset_mock()

            again = track_tft_account(
                "1", "Renamed", "TFT", "NA1", allow_reassign=True, known=first
            )

            self.assertEqual(again.puuid, first.puuid)
            self.assertEqual(again.server, "NA1")
            # The remembered history survives, so the next poll does not
            # re-announce games this account was already announced for.
            self.assertEqual(load_tft_tracker_state()["1"].matches, ["NA1_TFT_1"])
        client.tft_home_platform.assert_not_called()
        client.tft_match_ids.assert_not_called()

    def test_refreshing_a_moved_puuid_still_probes_and_reseeds(self) -> None:
        """A PUUID that no longer matches is a fresh registration, not a refresh."""
        stale = Account("1", "old-puuid", "EUW1", "Old#EUW")
        client = Mock()
        client.tft_puuid.return_value = "tft-puuid"
        client.tft_home_platform.return_value = "NA1"
        client.tft_riot_id.return_value = "Tactician#TFT"
        client.tft_match_ids.return_value = ["NA1_TFT_9"]
        with temporary_state(), patch(
            "bot_app.tft_account_registry.get_client", return_value=client
        ):
            account = track_tft_account(
                "1", "Tactician", "TFT", "NA1", allow_reassign=True, known=stale
            )
            self.assertEqual(account.server, "NA1")
            self.assertEqual(load_tft_tracker_state()["1"].matches, ["NA1_TFT_9"])
        client.tft_home_platform.assert_called_once()
        client.tft_match_ids.assert_called_once()

    def test_update_all_reports_progress_for_each_account(self) -> None:
        accounts = {
            "1": Account("1", "league-one", "NA1", "One#NA1"),
            "2": Account("2", "league-two", "EUW1", "Two#EUW"),
        }
        seen: list[tuple[int, int]] = []
        with (
            patch(
                "bot_app.tft_account_registry.load_accounts", return_value=accounts
            ),
            patch(
                "bot_app.tft_account_registry.update_tft_from_league",
                side_effect=RegistryError("nope"),
            ),
        ):
            update_all_tft_from_league(progress=lambda *item: seen.append(item))

        # Progress must advance even while every account is failing, or a
        # roster of dead accounts would look like a hung command.
        self.assertEqual(seen, [(1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()

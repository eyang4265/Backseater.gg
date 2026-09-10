"""Teammate PUUIDs: storage round-trip, conditional bolding, and listing."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot_app import store
from bot_app.account_registry import RegistryError
from bot_app.commands.add import (
    _demote_to_teammate,
    _promote_from_teammate,
    _register_teammate,
)
from bot_app.commands.registry import _account_line, _is_unlinked, _teammate_line
from bot_app.render import build_match_columns
from bot_app.store import (
    Account,
    load_accounts,
    load_teammates,
    save_accounts,
    teammate_puuids,
    update_teammates,
)

from tests.support import temporary_state


class SwitchRegistryTests(unittest.TestCase):
    def test_demote_moves_a_tracked_account_into_the_teammate_registry(self) -> None:
        with temporary_state() as root, patch.object(
            store, "TEAMMATE_DATA_PATH", root / "teammates.json"
        ):
            store._teammate_puuid_cache.invalidate()
            save_accounts({"55": Account("55", "p-league", "EUW1", "Real#EUW")})

            mate = _demote_to_teammate("55")

            self.assertEqual((mate.discord_id, mate.puuid, mate.server), ("55", "p-league", "EUW1"))
            self.assertNotIn("55", load_accounts())
            self.assertEqual(load_teammates()["p-league"].riot_id, "Real#EUW")
            self.assertEqual(teammate_puuids(), frozenset({"p-league"}))

    def test_demote_without_a_tracked_account_is_rejected(self) -> None:
        with temporary_state() as root, patch.object(
            store, "TEAMMATE_DATA_PATH", root / "teammates.json"
        ):
            with self.assertRaisesRegex(RegistryError, "no tracked account"):
                _demote_to_teammate("999")

    def test_promote_re_tracks_and_drops_the_teammate_entry(self) -> None:
        league = Account("55", "p-league", "NA1", "Real#NA1")
        tft = Account("55", "p-tft", "NA1", "Real#NA1")
        with temporary_state() as root, patch.object(
            store, "TEAMMATE_DATA_PATH", root / "teammates.json"
        ), patch(
            "bot_app.commands.add.track_account", return_value=league
        ), patch(
            "bot_app.commands.add.track_tft_account", return_value=tft
        ):
            store._teammate_puuid_cache.invalidate()
            update_teammates(
                lambda teammates: teammates.__setitem__(
                    "p-league", Account("55", "p-league", "NA1", "Real#NA1")
                )
            )

            result = _promote_from_teammate("55", 25)

            self.assertEqual(result, (league, tft))
            self.assertEqual(load_teammates(), {})


class AccountsListingTests(unittest.TestCase):
    def test_linked_account_renders_a_mention(self) -> None:
        line = _account_line(Account("123456789012345678", "p", "NA1", "Real#NA1"))
        self.assertEqual(line, "<@123456789012345678> — **Real#NA1** (NA1)")

    def test_unlinked_account_renders_a_marker(self) -> None:
        self.assertTrue(_is_unlinked("000000000000000001"))
        self.assertFalse(_is_unlinked("123456789012345678"))
        line = _account_line(Account("000000000000000001", "p", "EUW1", "Solo#EUW"))
        self.assertEqual(line, "*(unlinked)* — **Solo#EUW** (EUW1)")

    def test_teammate_line_shows_the_tie_only_when_present(self) -> None:
        tied = _teammate_line(Account("42", "p", "NA1", "Mate#NA1"))
        self.assertEqual(tied, "🤝 **Mate#NA1** (NA1) — <@42>")
        loose = _teammate_line(Account("", "p", "NA1", "Mate#NA1"))
        self.assertEqual(loose, "🤝 **Mate#NA1** (NA1)")


class _PlayerLookup:
    """Minimal stand-in for render._PlayerLookup."""

    def __init__(self, riot_id: str) -> None:
        self.riot_id = riot_id
        self.rank = None
        self.rank_unavailable = False


def _participant(puuid: str, name: str) -> dict[str, object]:
    return {
        "puuid": puuid,
        "teamId": 100,
        "championName": name,
        "kills": 1,
        "deaths": 1,
        "assists": 1,
    }


class TeammateStoreTests(unittest.TestCase):
    def test_round_trip_and_cache_invalidation(self) -> None:
        """Saved teammates reload, and the cached puuid set refreshes."""
        with temporary_state() as root, patch.object(
            store, "TEAMMATE_DATA_PATH", root / "teammates.json"
        ):
            store._teammate_puuid_cache.invalidate()
            self.assertEqual(teammate_puuids(), frozenset())

            update_teammates(
                lambda teammates: teammates.__setitem__(
                    "mate-puuid",
                    Account("123", "mate-puuid", "EUW1", "Mate#EUW"),
                )
            )

            reloaded = load_teammates()
            self.assertEqual(reloaded["mate-puuid"].server, "EUW1")
            self.assertEqual(reloaded["mate-puuid"].riot_id, "Mate#EUW")
            self.assertEqual(reloaded["mate-puuid"].discord_id, "123")
            self.assertEqual(teammate_puuids(), frozenset({"mate-puuid"}))

            update_teammates(
                lambda teammates: teammates.__setitem__(
                    "solo-puuid",
                    Account("", "solo-puuid", "NA1", "Solo#NA1"),
                )
            )
            self.assertEqual(load_teammates()["solo-puuid"].discord_id, "")
            self.assertEqual(
                teammate_puuids(), frozenset({"mate-puuid", "solo-puuid"})
            )

            update_teammates(lambda teammates: teammates.clear())
            self.assertEqual(teammate_puuids(), frozenset())

    def test_add_teammate_resolves_and_stores_tied_to_the_user(self) -> None:
        """`/add ... teammate:True` stores a bare PUUID tied to the user."""
        client = MagicMock()
        client.puuid.side_effect = lambda name, tag, platform: (
            "mate-puuid" if platform == "EUW1" else None
        )
        client.home_platform.return_value = "EUW1"
        client.riot_id.return_value = "Mate#EUW"
        with temporary_state() as root, patch.object(
            store, "TEAMMATE_DATA_PATH", root / "teammates.json"
        ), patch("bot_app.commands.add.get_client", return_value=client):
            store._teammate_puuid_cache.invalidate()
            account = _register_teammate("456", "Mate", "EUW")

            self.assertEqual(account.puuid, "mate-puuid")
            self.assertEqual(account.server, "EUW1")
            self.assertEqual(account.discord_id, "456")
            self.assertEqual(load_teammates()["mate-puuid"].discord_id, "456")
            self.assertEqual(teammate_puuids(), frozenset({"mate-puuid"}))


class TeammateBoldingTests(unittest.TestCase):
    _PARTICIPANTS = [
        _participant("tracked", "Ahri"),
        _participant("mate", "Lux"),
        _participant("stranger", "Zed"),
    ]

    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:c:1>")
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch(
        "bot_app.render._positions_by_index",
        return_value=["Top", "Jungle", "Middle"],
    )
    @patch(
        "bot_app.render._resolve_concurrently",
        side_effect=lambda items, fn: [
            _PlayerLookup(name)
            for name in ("Tracked#NA1", "Mate#NA1", "Stranger#NA1")
        ],
    )
    @patch("bot_app.render.teammate_puuids", return_value=frozenset({"mate"}))
    @patch("bot_app.render.tracked_puuids", return_value=frozenset({"tracked"}))
    def test_teammate_is_bolded_when_a_tracked_account_shares_the_lobby(
        self, *_mocks: object
    ) -> None:
        columns = build_match_columns(self._PARTICIPANTS, server="na1")
        by_name = dict(zip(("Tracked", "Mate", "Stranger"), columns.blue_names))
        self.assertTrue(by_name["Tracked"].startswith("**"))
        self.assertTrue(by_name["Mate"].startswith("**"))
        self.assertFalse(by_name["Stranger"].startswith("**"))

    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:c:1>")
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch(
        "bot_app.render._positions_by_index",
        return_value=["Top", "Jungle", "Middle"],
    )
    @patch(
        "bot_app.render._resolve_concurrently",
        side_effect=lambda items, fn: [
            _PlayerLookup(name)
            for name in ("Tracked#NA1", "Mate#NA1", "Stranger#NA1")
        ],
    )
    @patch("bot_app.render.teammate_puuids", return_value=frozenset({"mate"}))
    @patch("bot_app.render.tracked_puuids", return_value=frozenset())
    def test_teammate_is_not_bolded_without_a_tracked_account_present(
        self, *_mocks: object
    ) -> None:
        columns = build_match_columns(self._PARTICIPANTS, server="na1")
        self.assertNotIn("**", " ".join(columns.blue_names))


if __name__ == "__main__":
    unittest.main()

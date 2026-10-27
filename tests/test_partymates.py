"""Private inferred-partymate identities use atomic teammate-compatible storage."""

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from bot_app.store import (
    Account, load_partymates, remember_partymates, save_partymates,
    save_accounts, save_teammates,
)
from tests.support import temporary_state


class PartymateStorageTests(unittest.TestCase):
    def test_detection_and_direct_save_exclude_registered_puuids(self):
        with temporary_state() as root:
            tracked = Account("42", "tracked", "NA1", "Tracked#NA1")
            teammate = Account("", "mate", "NA1", "Mate#NA1")
            outsider = Account("", "party", "NA1", "Party#NA1")
            save_accounts({"42": tracked})
            save_teammates({"mate": teammate})
            self.assertEqual(set(remember_partymates([tracked, teammate, outsider])), {"party"})
            save_partymates({a.puuid: a for a in (tracked, teammate, outsider)})
            self.assertEqual(set(json.loads((root / "partymates.json").read_text())), {"party"})

    def test_legacy_overlaps_are_hidden_and_pruned_by_an_empty_merge(self):
        with temporary_state() as root:
            save_accounts({"42": Account("42", "tracked", "NA1", "Tracked#NA1")})
            save_teammates({"mate": Account("", "mate", "NA1", "Mate#NA1")})
            path = root / "partymates.json"
            path.write_text(json.dumps({p: {"server": "NA1", "riotId": p} for p in ("tracked", "mate", "party")}))
            self.assertEqual(set(load_partymates()), {"party"})
            remember_partymates([])
            self.assertEqual(set(json.loads(path.read_text())), {"party"})

    def test_registering_a_partymate_retires_the_stored_entry(self):
        with temporary_state() as root:
            a = Account("42", "a", "NA1", "Alice#NA1")
            b = Account("", "b", "NA1", "Bob#NA1")
            remember_partymates([a, b])
            save_accounts({"42": a})
            self.assertEqual(set(json.loads((root / "partymates.json").read_text())), {"b"})
            save_teammates({"b": b})
            self.assertEqual(json.loads((root / "partymates.json").read_text()), {})

    def test_round_trip_uses_puuid_keys_and_teammate_identity_fields(self):
        with temporary_state() as root:
            account = Account("123", "puuid", "EUW1", "Player#EUW")
            save_partymates({"puuid": account})
            self.assertEqual(load_partymates(), {"puuid": account})
            self.assertEqual(json.loads((root / "partymates.json").read_text()), {
                "puuid": {"server": "EUW1", "riotId": "Player#EUW", "discordId": "123"}})
            self.assertFalse((root / "partymates.json.tmp").exists())

    def test_merge_preserves_known_ids_and_skips_unchanged_writes(self):
        with temporary_state():
            original = Account("123", "a", "NA1", "Alice#NA1")
            remember_partymates([original])
            with patch("bot_app.store.save_partymates") as save:
                remember_partymates([Account("", "a", "NA1", "")])
                save.assert_not_called()
            remember_partymates([Account("", "b", "KR", "Bob#KR")])
            self.assertEqual(load_partymates()["a"], original)
            self.assertEqual(set(load_partymates()), {"a", "b"})

    def test_concurrent_merges_do_not_lose_ids(self):
        with temporary_state():
            accounts = [Account("", str(i), "NA1", f"Player{i}#NA1") for i in range(20)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda account: remember_partymates([account]), accounts))
            self.assertEqual(set(load_partymates()), {str(i) for i in range(20)})

    def test_missing_and_malformed_storage_degrades_safely(self):
        with temporary_state() as root:
            self.assertEqual(load_partymates(), {})
            path = root / "partymates.json"
            path.write_text('{"bad": null, "good": {"riotId": "Player#NA1"}}')
            self.assertEqual(set(load_partymates()), {"good"})
            path.write_text('[]')
            self.assertEqual(load_partymates(), {})

    def test_partymates_do_not_become_tracked_accounts(self):
        from bot_app.store import load_accounts
        with temporary_state() as root:
            remember_partymates([Account("", "a", "NA1", "Alice#NA1")])
            self.assertEqual(load_accounts(), {})
            self.assertFalse((root / "data.json").exists())

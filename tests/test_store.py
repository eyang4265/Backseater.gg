"""Poller state: match-id retention and the legacy on-disk formats."""

import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from bot_app.ranks import RankSnapshot
from bot_app.store import (
    PlayerState,
    TftPlayerState,
    dedupe_tail,
    load_embed_button_states,
    load_live_game_messages,
    load_live_game_state,
    pop_live_game_messages,
    remember_live_game_message,
    save_live_game_state,
    league_state_transaction,
)
from bot_app.config import migrate_legacy_runtime_file, migrate_legacy_sqlite_file


class RuntimeStorageTests(unittest.TestCase):
    def test_legacy_file_is_copied_without_removing_its_backup(self) -> None:
        """An upgrade preserves both the live copy and its migration source."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "json" / "state.json"
            target = root / "data" / "state.json"
            legacy.parent.mkdir()
            legacy.write_text('{"match": 1}', encoding="utf-8")

            migrate_legacy_runtime_file(target, legacy)

            self.assertEqual(target.read_text(encoding="utf-8"), '{"match": 1}')
            self.assertTrue(legacy.exists())

    def test_newer_legacy_file_wins_during_running_bot_cutover(self) -> None:
        """The old process may keep writing JSON until the upgraded restart."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "json" / "state.json"
            target = root / "data" / "state.json"
            legacy.parent.mkdir()
            target.parent.mkdir()
            target.write_text("old", encoding="utf-8")
            legacy.write_text("new", encoding="utf-8")
            target.touch()
            legacy.touch()
            # Ensure a strict timestamp order even on coarse filesystems.
            import os
            os.utime(target, (1, 1))
            os.utime(legacy, (2, 2))

            migrate_legacy_runtime_file(target, legacy)

            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_sqlite_migration_uses_a_consistent_backup(self) -> None:
        """Database migration copies committed rows through SQLite itself."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "json" / "state.sqlite"
            target = root / "data" / "state.sqlite"
            legacy.parent.mkdir()
            with sqlite3.connect(str(legacy)) as database:
                database.execute("CREATE TABLE state (value TEXT NOT NULL)")
                database.execute("INSERT INTO state VALUES ('kept')")

            migrate_legacy_sqlite_file(target, legacy)

            with sqlite3.connect(str(target)) as database:
                self.assertEqual(
                    database.execute("SELECT value FROM state").fetchone()[0],
                    "kept",
                )

    def test_league_state_transactions_do_not_overlap(self) -> None:
        """Concurrent pollers cannot commit stale League state snapshots."""
        first_entered = Event()
        release_first = Event()
        second_entered = Event()

        def first() -> None:
            with league_state_transaction():
                first_entered.set()
                release_first.wait(timeout=2)

        def second() -> None:
            first_entered.wait(timeout=2)
            with league_state_transaction():
                second_entered.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first)
            second_future = executor.submit(second)
            self.assertTrue(first_entered.wait(timeout=2))
            self.assertFalse(second_entered.wait(timeout=0.05))
            release_first.set()
            first_future.result(timeout=2)
            second_future.result(timeout=2)
        self.assertTrue(second_entered.is_set())


class ComponentStateMigrationTests(unittest.TestCase):
    def test_legacy_json_is_imported_when_sqlite_is_empty(self) -> None:
        """Existing persistent views migrate without being discarded."""
        state = {
            "message_id": 1,
            "channel_id": 2,
            "kind": "match",
            "payload": {"match": {}},
        }
        cache = Mock()
        cache.load_embed_button_states.side_effect = [[], [state]]
        cache.import_embed_button_states.return_value = 1
        with (
            patch("bot_app.match_cache.get_match_cache", return_value=cache),
            patch("bot_app.store.read_json", return_value=[state]),
        ):
            self.assertEqual(load_embed_button_states(), [state])
        cache.import_embed_button_states.assert_called_once_with([state])


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
            matches=["NA1_1"],
            ranks={420: RankSnapshot("IRON", "IV", 1)},
        )
        restored = PlayerState.from_json(state.to_json())
        self.assertEqual(restored.matches, state.matches)
        self.assertEqual(
            restored.ranks[420],
            RankSnapshot("IRON", "IV", 1),
        )

    def test_tft_state_round_trips_independently(self) -> None:
        state = TftPlayerState(["NA1_TFT_1"], True)
        self.assertEqual(TftPlayerState.from_json(state.to_json()), state)

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


class LiveGameMessageRecordTests(unittest.TestCase):
    def test_remember_then_pop_round_trips_and_clears(self) -> None:
        """Recorded posts come back once, keyed by lobby, then are gone."""
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from pathlib import Path

        with (
            TemporaryDirectory() as directory,
            patch(
                "bot_app.store.LIVE_GAME_MESSAGE_PATH",
                Path(directory) / "live_messages.json",
            ),
        ):
            remember_live_game_message("NA1:1", 10, 100)
            remember_live_game_message("NA1:1", 11, 101)
            remember_live_game_message("NA1:1", 10, 100)  # deduped
            remember_live_game_message("NA1:2", 12, 102)

            self.assertEqual(
                load_live_game_messages(),
                {"NA1:1": [[10, 100], [11, 101]], "NA1:2": [[12, 102]]},
            )

            popped = pop_live_game_messages(["NA1:1", "NA1:missing"])
            self.assertCountEqual(popped, [(10, 100), (11, 101)])
            self.assertEqual(load_live_game_messages(), {"NA1:2": [[12, 102]]})
            self.assertEqual(pop_live_game_messages(["NA1:1"]), [])


if __name__ == "__main__":
    unittest.main()

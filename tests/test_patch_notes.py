"""The bot presents maintained patch notes without relying on Git history."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot_app.services.patch_notes import (
    PatchNotesUnavailable,
    parse_patch_notes,
    recent_bot_changes,
)


class PatchNotesTests(unittest.TestCase):
    def test_newest_dated_changes_win(self) -> None:
        notes = """# Bot Patch Notes
## 2026-09-18
- Added one feature.
- Improved another feature.
## 2026-09-15
- Fixed an older issue.
"""
        self.assertEqual(
            [(change.date, change.summary) for change in parse_patch_notes(notes, 2)],
            [
                ("2026-09-18", "Added one feature."),
                ("2026-09-18", "Improved another feature."),
            ],
        )

    def test_file_changes_are_visible_without_committing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "PATCH_NOTES.md"
            with patch("bot_app.services.patch_notes.PATCH_NOTES_PATH", path):
                path.write_text("## 2026-09-18\n- First update.\n", encoding="utf-8")
                self.assertEqual(recent_bot_changes()[0].summary, "First update.")
                path.write_text("## 2026-09-18\n- New update.\n", encoding="utf-8")
                self.assertEqual(recent_bot_changes()[0].summary, "New update.")

    def test_missing_notes_are_reported(self) -> None:
        with self.assertRaises(PatchNotesUnavailable):
            parse_patch_notes("# Bot Patch Notes\n")

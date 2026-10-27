"""The bot presents maintained patch notes without relying on Git history."""

import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.commands.patchnotes import PatchNotesCommands, patch_notes_pages

from bot_app.services.patch_notes import (
    PatchNotesUnavailable,
    BotChange,
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

    def test_full_party_tracking_entry_is_sent_after_acknowledgement(self):
        async def exercise():
            changes = [BotChange("2026-10-06", (
                "Added party tracking: repeated teammates can be saved as inferred "
                "partymates on their third live game together, after two qualifying "
                "same-team games in the same queue within seven days. Partymates "
                "have bold names with 👥 in match and live announcements. Tracked "
                "players and linked teammates share the ⭐ marker."
            ))]
            self.assertGreater(len(changes[0].summary), 180)
            ctx = Mock(author=Mock(id=42), guild=Mock(id=1))
            ctx.defer, ctx.respond = AsyncMock(), AsyncMock()

            def read():
                ctx.defer.assert_awaited_once()
                return changes

            with patch("bot_app.commands.patchnotes.recent_bot_changes", side_effect=read):
                await PatchNotesCommands.patchnotes.callback(PatchNotesCommands(Mock()), ctx)
            descriptions = "".join(c.kwargs["embed"].description for c in ctx.respond.call_args_list)
            full = discord.utils.escape_mentions(discord.utils.escape_markdown(changes[0].summary))
            self.assertIn(full, descriptions)
            self.assertIn("⭐ marker.", descriptions)

        asyncio.run(exercise())

    def test_oversized_entries_split_without_losing_text_or_mention_escaping(self):
        changes = [BotChange("2026-10-06", "@everyone " + "Word " * 1100),
                   BotChange("2026-10-05", "x" * 8500)]
        pages = patch_notes_pages(changes)
        self.assertTrue(all(len(page) <= 4000 for page in pages))
        self.assertGreater(len(pages), 2)
        expected = "".join(
            f"**{c.date}** — {discord.utils.escape_mentions(discord.utils.escape_markdown(c.summary))}"
            for c in changes
        )
        self.assertEqual("".join(pages).replace("\n\n", ""), expected)

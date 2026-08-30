"""Tests for the compact public command guide."""

import unittest

from bot_app.commands.info.commands import (
    _COMMAND_LOOKUP,
    _entry_details,
    _entry_summary,
    _field_chunks,
)


class HelpFormattingTests(unittest.TestCase):
    """Keep the overview concise and detailed help easy to scan."""

    def test_overview_summary_omits_usage(self) -> None:
        """The directory should show only one purpose line per command."""
        summary = _entry_summary(_COMMAND_LOOKUP["matchhistory"])

        self.assertIn("/matchhistory", summary)
        self.assertNotIn("Usage:", summary)
        self.assertNotIn("\n", summary)

    def test_details_separate_purpose_and_usage(self) -> None:
        """Focused help should not repeat the command name in its description."""
        purpose, usage = _entry_details(_COMMAND_LOOKUP["profile"])

        self.assertEqual(purpose, "Show level, ranks, and top champions.")
        self.assertEqual(
            usage, "add `server` and `username` (accepts `Name#Tag`)"
        )

    def test_field_chunks_split_only_between_commands(self) -> None:
        """Compact category fields must stay within Discord's value limit."""
        entries = ("first command", "second command", "third command")

        chunks = _field_chunks(entries, limit=29)

        self.assertEqual(chunks, ("first command\nsecond command", "third command"))
        self.assertTrue(all(len(chunk) <= 29 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()

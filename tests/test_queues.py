"""Curated current-game-mode allowlist backing /matchhistory's game_mode filter."""

import unittest
from unittest.mock import patch

from bot_app.queues import (
    CURRENT_QUEUE_IDS,
    QUEUE_NAMES,
    current_queue_names,
    validate_current_queue_ids,
)


class CurrentQueueNamesTests(unittest.TestCase):
    def test_every_current_id_has_a_known_name(self) -> None:
        """Verify that the shipped allowlist has no stale/typo'd queue ids."""
        self.assertEqual(validate_current_queue_ids(), ())

    def test_returns_names_for_exactly_the_current_ids(self) -> None:
        """Verify that current_queue_names reflects CURRENT_QUEUE_IDS, not the full table."""
        expected = sorted({QUEUE_NAMES[queue_id] for queue_id in CURRENT_QUEUE_IDS})
        self.assertEqual(list(current_queue_names()), expected)
        # Some known modes (e.g. rotating events) should NOT be offered.
        self.assertNotIn("Nexus Blitz", current_queue_names())
        self.assertIn("Ranked Solo/Duo", current_queue_names())

    def test_shared_name_survives_if_either_id_is_current(self) -> None:
        """Verify a name mapped from multiple ids appears if any one of them is current."""
        # "Arena" is shared by 1700 and 1710; only 1700 needs to stay current.
        with patch("bot_app.queues.CURRENT_QUEUE_IDS", frozenset({1700})):
            self.assertIn("Arena", current_queue_names())

    def test_validate_flags_a_stale_id(self) -> None:
        """Verify that an id with no QUEUE_NAMES entry is reported, not silently dropped."""
        with patch("bot_app.queues.CURRENT_QUEUE_IDS", frozenset({420, 999999})):
            self.assertEqual(validate_current_queue_ids(), (999999,))


if __name__ == "__main__":
    unittest.main()

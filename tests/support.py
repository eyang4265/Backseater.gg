"""Shared network-free test helpers."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


@contextmanager
def temporary_state():
    """Handle state."""
    with TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        for name, filename in {
            "DATA_PATH": "data.json",
            "TFT_DATA_PATH": "tft_data.json",
            "PARTYMATE_DATA_PATH": "partymates.json",
            "TEAMMATE_DATA_PATH": "teammates.json",
            "TRACKER_STATE_PATH": "tracker.json",
            "PROCESSED_MATCH_IDS_PATH": "processed_matches.json",
            "TFT_TRACKER_STATE_PATH": "tft_tracker.json",
            "LIVE_GAME_STATE_PATH": "live.json",
            "LIVE_GAME_MESSAGE_PATH": "live_messages.json",
            "LIVE_GAME_SNAPSHOT_PATH": "live_snapshots.json",
            "TFT_LIVE_GAME_STATE_PATH": "tft_live.json",
            "GUILD_STATE_PATH": "guilds.json",
        }.items():
            stack.enter_context(patch(f"bot_app.store.{name}", root / filename))
        stack.enter_context(patch("bot_app.daily_summary.STATE_PATH", root / "daily_summary.json"))
        yield root

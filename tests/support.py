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
            "TRACKER_STATE_PATH": "tracker.json",
            "GUEST_STATE_PATH": "guest.json",
            "LIVE_GAME_STATE_PATH": "live.json",
            "GUILD_STATE_PATH": "guilds.json",
        }.items():
            stack.enter_context(patch(f"bot_app.store.{name}", root / filename))
        yield root

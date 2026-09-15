"""Guard against running more than one bot process at a time.

A second instance racing the first to sync slash commands can exhaust
Discord's per-guild command-sync rate limit within seconds, so the process
exits immediately instead of starting up alongside an existing one.
"""

from __future__ import annotations

import fcntl
import logging
import sys
from pathlib import Path
from typing import IO

LOGGER = logging.getLogger(__name__)

_LOCK_PATH = Path(__file__).resolve().parent.parent / "bot.lock"
_lock_file: IO[str] | None = None


def acquire_singleton_lock() -> None:
    """Exit the process if another bot instance already holds the lock."""
    global _lock_file
    _lock_file = _LOCK_PATH.open("w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        LOGGER.debug(
            "Acquired singleton lock at %s",
            _LOCK_PATH,
            extra={"category": "STARTUP"},
        )
    except OSError:
        LOGGER.error(
            "Another bot instance is already running (lock held on %s)."
            " Stop it before starting a new one.",
            _LOCK_PATH,
            extra={"category": "STARTUP"},
        )
        _lock_file.close()
        _lock_file = None
        sys.exit(1)

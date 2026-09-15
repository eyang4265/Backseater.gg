"""Hermetic defaults shared by the offline unit-test suite."""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path


_matplotlib_cache = Path(tempfile.gettempdir()) / "vibecode-bot-matplotlib-tests"
_matplotlib_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_matplotlib_cache))


def _blocked_connect(self, address):
    """Prevent accidental external I/O while allowing mocked HTTP clients."""
    raise AssertionError(
        "Network access is disabled in unit tests; mock the adapter or set "
        "VIBECODE_TEST_NETWORK=1 for an explicit integration run"
    )


def _blocked_getaddrinfo(*args, **kwargs):
    """Block DNS too; urllib3 resolves names before opening its socket."""
    raise AssertionError(
        "DNS/network access is disabled in unit tests; mock the adapter or set "
        "VIBECODE_TEST_NETWORK=1 for an explicit integration run"
    )


if os.environ.get("VIBECODE_TEST_NETWORK") != "1":
    socket.getaddrinfo = _blocked_getaddrinfo
    socket.socket.connect = _blocked_connect

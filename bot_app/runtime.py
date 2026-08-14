"""Access to the initialized Discord client.

The client is created in ``main`` and registered here. Modules call
``get_bot()`` at *call* time rather than importing the object at module
import time, which is what let the old layout depend on command modules
being imported strictly after ``configure_bot()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import discord

_bot: "discord.Bot | None" = None


def configure_bot(bot: "discord.Bot") -> None:
    global _bot
    _bot = bot


def get_bot() -> "discord.Bot | None":
    """The registered client, or None before ``configure_bot`` has run.

    Returning None rather than raising keeps presentation helpers usable in
    tests and in ``/selftest`` paths that render without a live client.
    """
    return _bot

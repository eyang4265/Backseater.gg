"""Custom-emoji lookup across every guild the bot is in.

Emotes often live on a dedicated hosting server rather than the guild a
command ran in, so all of them are searched. The old helpers walked
``bot.emojis`` once per participant; here the names are indexed once and
rebuilt only when the client's emoji cache changes size.
"""

from __future__ import annotations

import threading
from typing import Any

from .ddragon import Champion

_lock = threading.Lock()
_index: dict[str, Any] = {}
_indexed_count = -1


def _emoji_index() -> dict[str, Any]:
    from .runtime import get_bot

    bot = get_bot()
    if bot is None:
        return {}

    emojis = bot.emojis
    global _index, _indexed_count
    with _lock:
        if len(emojis) != _indexed_count:
            _index = {emoji.name.lower(): emoji for emoji in emojis}
            _indexed_count = len(emojis)
        return _index


def champion_emoji(champion: Champion | None, *, name: str | None = None) -> Any | None:
    """Emoji for a champion, matched on internal id then display name.

    ``name`` covers callers that only have a display string (e.g. a match
    payload's raw ``championName``) and no catalog entry.
    """
    index = _emoji_index()
    if not index:
        return None

    candidates: list[str] = []
    if champion is not None:
        candidates += [champion.internal_id, champion.name, champion.name.replace(" ", "").replace("'", "")]
    if name:
        candidates += [name, name.replace(" ", "").replace("'", "")]

    for candidate in candidates:
        found = index.get(candidate.lower())
        if found is not None:
            return found
    return None


def rank_emoji(tier: str | None) -> Any | None:
    """Emoji named after a ranked tier (``PLATINUM`` -> an emoji called ``platinum``)."""
    if not tier:
        return None
    return _emoji_index().get(tier.lower())


def prefixed(emoji: Any | None, text: str) -> str:
    return f"{emoji} {text}" if emoji else text

"""Custom-emoji lookup across every guild the bot is in.

Emotes often live on a dedicated hosting server rather than the guild a
command ran in, so all of them are searched. The old helpers walked
``bot.emojis`` once per participant; here the names are indexed once and
rebuilt only when the client's emoji cache changes size.
"""

from __future__ import annotations

import logging
import threading
import re
from typing import Any

from .ddragon import Champion

LOGGER = logging.getLogger(__name__)

_lock = threading.Lock()
_index: dict[str, Any] = {}
_indexed_count = -1

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_NON_ALNUM_RUN_RE = re.compile(r"[^a-z0-9]+")

_RUNE_ALIASES = {
    "adaptive force": "AdaptiveForce",
    "adaptive force scaling": "AdaptiveForceScaling",
    "attack speed": "AttackSpeed",
    "armor": "Armor",
    "ability haste": "CDRScaling",
    "cooldown reduction": "CDRScaling",
    "health": "HealthPlus",
    "health scaling": "HealthScaling",
    "magic resist": "MagicRes",
    "move speed": "MovementSpeed",
    "movement speed": "MovementSpeed",
    "tenacity": "Tenacity",
    "nimbus cloak": "NimbusCloak",
}

_SUMMONER_SPELL_NAMES = {
    "1": "SummonerBoost",  # Cleanse
    "3": "SummonerExhaust",
    "4": "SummonerFlash",
    "6": "SummonerHaste",  # Ghost
    "7": "SummonerHeal",
    "11": "SummonerSmite",
    "12": "SummonerTeleport",
    "13": "SummonerMana",  # Clarity
    "14": "SummonerDot",  # Ignite
    "21": "SummonerBarrier",
    "32": "SummonerSnowball",
    "39": "SummonerSnowURFSnowball_Mark",
}


def _emoji_index() -> dict[str, Any]:
    """Handle index."""
    from .runtime import get_bot

    bot = get_bot()
    if bot is None:
        return {}

    emojis = bot.emojis
    global _index, _indexed_count
    with _lock:
        if len(emojis) != _indexed_count:
            LOGGER.debug(
                "Rebuilding emoji index: %s -> %s emojis", _indexed_count, len(emojis)
            )
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
        candidates += [
            champion.internal_id,
            champion.name,
            champion.name.replace(" ", "").replace("'", ""),
        ]
    if name:
        candidates += [name, name.replace(" ", "").replace("'", "")]

    for candidate in candidates:
        found = index.get(candidate.lower())
        if found is not None:
            return found
    LOGGER.debug("No champion emoji found for candidates=%s", candidates)
    return None


def named_emoji(name: str | None, *, prefixes: tuple[str, ...] = ()) -> Any | None:
    """Find a custom emoji by a display name and common configured prefixes."""
    if not name:
        return None
    index = _emoji_index()
    if not index:
        return None
    lowered = name.lower()
    compact = _NON_ALNUM_RE.sub("", lowered)
    underscored = _NON_ALNUM_RUN_RE.sub("_", lowered).strip("_")
    candidates = [name.lower(), compact, underscored]
    candidates.extend(
        f"{prefix}{candidate}"
        for prefix in prefixes
        for candidate in (name.lower(), compact, underscored)
    )
    for candidate in candidates:
        found = index.get(candidate)
        if found is not None:
            return found
    return None


def item_emoji(name: str | None, *, item_id: str | int | None = None) -> Any | None:
    """Find an item emoji by id, including Stormrazor's older id, then name."""
    if item_id is not None:
        normalized_id = str(item_id)
        if normalized_id.startswith("32"):
            # Riot's 32xxxx entries are alternate-map variants; prefer the
            # standard item asset for consistent champion build rendering.
            candidate_ids = [normalized_id[2:], normalized_id]
        else:
            candidate_ids = [normalized_id, f"32{normalized_id}"]
        if normalized_id == "3095":
            # Older matches retain Stormrazor's former id; the configured
            # icon uses its current 3097 id.
            candidate_ids.extend(("3097", "323097"))
        for candidate_id in candidate_ids:
            found = named_emoji(candidate_id)
            if found is not None:
                return found
    # OP.GG's core-build payload can omit the ID for Muramana while still
    # returning its display name. Prefer the standard item asset explicitly.
    if name and _NON_ALNUM_RE.sub("", name.lower()) == "muramana":
        for candidate_id in ("3004", "323004"):
            found = named_emoji(candidate_id)
            if found is not None:
                return found
    found = named_emoji(name, prefixes=("item_", "item"))
    if found is not None:
        return found
    if str(item_id) in {"3095", "3097"} or (name and name.casefold() == "stormrazor"):
        return named_emoji("Stormrazer", prefixes=("item_", "item"))
    return None


def rune_emoji(name: str | None) -> Any | None:
    """Find the configured custom emoji for a League rune."""
    found = named_emoji(name, prefixes=("rune_", "rune"))
    if found is not None:
        return found
    # OP.GG has used both its display labels and the in-game stat names here.
    alias = _RUNE_ALIASES.get(name.casefold()) if name else None
    if alias:
        found = named_emoji(alias)
        if found is not None:
            return found
    # OP.GG reports shard names as human-readable stats while the supplied
    # shard assets use Riot's StatMods*Icon names.
    compact = _NON_ALNUM_RE.sub("", name.lower()) if name else ""
    for candidate in (
        f"StatMods{compact}Icon",
        f"StatMods{compact}ScalingIcon",
    ):
        found = named_emoji(candidate)
        if found is not None:
            return found
    # The attached asset for the legacy keystone uses an explicit suffix.
    return named_emoji(
        f"{name}Keystone" if name else None,
        prefixes=("rune_", "rune"),
    )


def summoner_spell_emoji(spell_id: str | int | None) -> Any | None:
    """Find a summoner-spell emoji using the supplied Riot asset filenames."""
    if spell_id is None:
        return None
    name = str(spell_id)
    return named_emoji(
        name if name.startswith("Summoner") else _SUMMONER_SPELL_NAMES.get(name)
    )


def rank_emoji(tier: str | None) -> Any | None:
    """Emoji named after a ranked tier (``PLATINUM`` -> an emoji called ``platinum``)."""
    if not tier:
        return None
    return _emoji_index().get(tier.lower())


def prefixed(emoji: Any | None, text: str) -> str:
    """Handle prefixed."""
    return f"{emoji} {text}" if emoji else text

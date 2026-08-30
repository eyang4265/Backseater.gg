"""Queue ids, their display names, and which of them are ranked.

Previously this table existed twice (``live_game.QUEUE_NAMES`` and two copies
of ``_SELFTEST_RANKED_QUEUES``), which had already drifted apart.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

SOLO_QUEUE_ID = 420
FLEX_QUEUE_ID = 440
ARENA_QUEUE_IDS = frozenset({1700, 1710, 1750})
ARENA_TEAM_SIZES: dict[int, int] = {1700: 2, 1710: 2, 1750: 3}
RANKED_QUEUE_IDS = frozenset({SOLO_QUEUE_ID, FLEX_QUEUE_ID})


QUEUE_TYPES: dict[int, str] = {
    SOLO_QUEUE_ID: "RANKED_SOLO_5x5",
    FLEX_QUEUE_ID: "RANKED_FLEX_SR",
}

QUEUE_NAMES: dict[int, str] = {
    400: "Normal (Draft)",
    420: "Ranked Solo/Duo",
    430: "Normal (Blind)",
    440: "Ranked Flex",
    450: "ARAM",
    480: "Swiftplay",
    490: "Normal (Quickplay)",
    600: "Blood Hunt Assassin",
    610: "Dark Star: Singularity",
    700: "Clash",
    720: "ARAM Clash",
    830: "Co-op vs AI (Intro)",
    840: "Co-op vs AI (Beginner)",
    850: "Co-op vs AI (Intermediate)",
    870: "Co-op vs AI (Intro)",
    880: "Co-op vs AI (Beginner)",
    890: "Co-op vs AI (Intermediate)",
    900: "URF",
    1020: "One for All",
    1300: "Nexus Blitz",
    1400: "Ultimate Spellbook",
    1700: "Arena",
    1710: "Arena",
    1750: "Arena (3v3)",
    1810: "Swarm (1 Player)",
    1820: "Swarm (2 Players)",
    1830: "Swarm (3 Players)",
    1840: "Swarm (4 Players)",
    1900: "URF",
    2300: "Brawl",
    2400: "ARAM: Mayhem",
}


def queue_name(queue_id: int | None) -> str:
    """Handle name."""
    if queue_id is None:
        return "Unknown Queue"
    return QUEUE_NAMES.get(queue_id, f"Queue {queue_id}")


def lobby_queue_name(queue_id: int | None) -> str:
    """Queue label for a live lobby, where an unmapped id is usually a custom game."""
    if queue_id is None:
        return "Unknown Queue"
    return QUEUE_NAMES.get(queue_id, f"Custom/Other Game (Queue {queue_id})")


def is_ranked(queue_id: int | None) -> bool:
    """Handle ranked."""
    return queue_id in RANKED_QUEUE_IDS


# Queue ids offered by the /matchhistory and /duo game_mode filters. Keep this
# intentionally limited to the supported modes shown to users; bot startup
# still validates the ids against QUEUE_NAMES.
CURRENT_QUEUE_IDS: frozenset[int] = frozenset(
    {
        400,  # Normal (Draft)
        420,  # Ranked Solo/Duo
        440,  # Ranked Flex
        450,  # ARAM
        1700,  # Arena
        1710,  # Arena
    }
)


def current_queue_names() -> tuple[str, ...]:
    """Game mode names for the queue ids in CURRENT_QUEUE_IDS."""
    return tuple(
        sorted({QUEUE_NAMES[queue_id] for queue_id in CURRENT_QUEUE_IDS if queue_id in QUEUE_NAMES})
    )


def validate_current_queue_ids() -> tuple[int, ...]:
    """Return any CURRENT_QUEUE_IDS entries with no matching QUEUE_NAMES entry.

    Meant to be checked once at bot startup, so an edit that leaves a stale or
    mistyped id in the allowlist logs a warning instead of the mode it was
    meant to add just silently never showing up.
    """
    stale = tuple(sorted(queue_id for queue_id in CURRENT_QUEUE_IDS if queue_id not in QUEUE_NAMES))
    if stale:
        LOGGER.warning("CURRENT_QUEUE_IDS has unmapped queue ids: %s", stale)
    else:
        LOGGER.debug("CURRENT_QUEUE_IDS validated: %d queue ids all mapped", len(CURRENT_QUEUE_IDS))
    return stale

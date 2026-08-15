"""Queue ids, their display names, and which of them are ranked.

Previously this table existed twice (``live_game.QUEUE_NAMES`` and two copies
of ``_SELFTEST_RANKED_QUEUES``), which had already drifted apart.
"""

from __future__ import annotations

SOLO_QUEUE_ID = 420
FLEX_QUEUE_ID = 440
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

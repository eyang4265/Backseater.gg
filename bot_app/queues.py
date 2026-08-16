"""Queue ids, their display names, and which of them are ranked.

Previously this table existed twice (``live_game.QUEUE_NAMES`` and two copies
of ``_SELFTEST_RANKED_QUEUES``), which had already drifted apart.
"""

from __future__ import annotations

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


# Queue ids currently queueable in the live client, offered by /matchhistory's
# game_mode filter. League rotates modes in and out with patches and events,
# and there's no reliable public API for "what's queueable right now" (Riot's
# queues.json leaves most retired rotating modes unmarked) — so this is a
# hand-maintained allowlist. Update it when a mode is added, retired, or its
# queue id changes; bot startup calls validate_current_queue_ids() to catch a
# stale/typo'd id here (one that no longer maps to a QUEUE_NAMES entry).
CURRENT_QUEUE_IDS: frozenset[int] = frozenset(
    {
        400,  # Normal (Draft)
        420,  # Ranked Solo/Duo
        430,  # Normal (Blind)
        440,  # Ranked Flex
        450,  # ARAM
        480,  # Swiftplay
        490,  # Normal (Quickplay)
        700,  # Clash
        870,  # Co-op vs AI (Intro)
        880,  # Co-op vs AI (Beginner)
        890,  # Co-op vs AI (Intermediate)
        1700,  # Arena
        1710,  # Arena
        2300,  # Brawl
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
    return tuple(sorted(queue_id for queue_id in CURRENT_QUEUE_IDS if queue_id not in QUEUE_NAMES))

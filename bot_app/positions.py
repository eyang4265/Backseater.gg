"""Inferring which lane each player is in.

The Spectator API doesn't report lane assignments — that only exists post-game
in match-v5 — so a live lobby's rows have to be inferred. Smite is a reliable
signal for Jungle; the rest is a best-effort read of Data Dragon's champion
tags, resolved for the whole team at once so each team always gets exactly one
of each role.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, Mapping, Sequence

LOGGER = logging.getLogger(__name__)

SMITE_SPELL_ID = 11

ROLE_ORDER: tuple[str, ...] = ("Top", "Jungle", "Mid", "Bottom", "Support")
_ROLE_INDEX = {role: index for index, role in enumerate(ROLE_ORDER)}


POSITION_LABELS: dict[str, str] = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "Bottom",
    "UTILITY": "Support",
}


_SINGLE_TAG_ROLES: dict[str, tuple[str, ...]] = {
    "Assassin": ("Mid",),
    "Fighter": ("Top",),
    "Mage": ("Mid",),
    "Marksman": ("Bottom",),
    "Support": ("Support",),
    "Tank": ("Top",),
}


_TAG_PAIR_ROLES: dict[tuple[str, str], tuple[str, ...]] = {
    ("Assassin", "Fighter"): ("Mid",),
    ("Assassin", "Mage"): ("Mid",),
    ("Fighter", "Assassin"): ("Top", "Mid"),
    ("Fighter", "Mage"): ("Top",),
    ("Fighter", "Marksman"): ("Top", "Mid"),
    ("Fighter", "Tank"): ("Top",),
    ("Mage", "Assassin"): ("Mid",),
    ("Mage", "Fighter"): ("Mid", "Top"),
    ("Mage", "Marksman"): ("Mid", "Top"),
    ("Mage", "Support"): ("Mid", "Support"),
    ("Marksman", "Assassin"): ("Bottom", "Mid"),
    ("Marksman", "Mage"): ("Bottom",),
    ("Marksman", "Support"): ("Bottom", "Support"),
    ("Support", "Assassin"): ("Support", "Mid"),
    ("Support", "Mage"): ("Support",),
    ("Support", "Marksman"): ("Support", "Bottom"),
    ("Tank", "Fighter"): ("Top",),
    ("Tank", "Mage"): ("Top",),
    ("Tank", "Support"): ("Support",),
}


_ROLE_OVERRIDES: dict[str, tuple[str, ...]] = {
    "Akshan": ("Mid",),
    "Elise": ("Support",),
    "Kennen": ("Top",),
    "Nilah": ("Bottom",),
    "Quinn": ("Top",),
    "Shaco": ("Top", "Support"),
    "Vayne": ("Top", "Bottom"),
}

_DEFAULT_ROLES: tuple[str, ...] = ("Top",)


def role_preference(champion_internal_id: str, tags: Sequence[str]) -> tuple[str, ...]:
    """Roles a champion fits, best first."""
    override = _ROLE_OVERRIDES.get(champion_internal_id)
    if override:
        return override
    if len(tags) >= 2:
        pair = _TAG_PAIR_ROLES.get((tags[0], tags[1]))
        if pair:
            return pair
    if tags:
        single = _SINGLE_TAG_ROLES.get(tags[0])
        if single:
            return single
    return _DEFAULT_ROLES


def _fit_score(role: str, champion_internal_id: str, tags: Sequence[str]) -> int:
    """How well a role suits a champion; Top is the catch-all so every role fills."""
    preferences = role_preference(champion_internal_id, tags)
    if role in preferences:
        return max(3 - preferences.index(role), 1)
    return 1 if role == "Top" else 0


def assign_team_positions(
    participants: Sequence[dict[str, Any]],
    champion_internal_ids: Sequence[str],
    champion_tags: Mapping[str, Sequence[str]],
    known_positions: Sequence[str | None] | None = None,
) -> list[str]:
    """Give each of a team's players exactly one role — no gaps, no duplicates.

    Guessing per player in isolation could leave a team with two Mids and no
    Top, and since rows are ordered by whichever roles are present, that
    shifted every row below the gap. Resolving the team together avoids it.

    Signals are applied strongest first: Smite (unambiguous, and more reliable
    than Riot's own ``teamPosition``, which mislabels invade and duo-jungle
    starts), then Riot's reported position where available, then champion tags
    — scored across every permutation of the roles still open, at most 5! but
    in practice a handful.
    """
    count = len(participants)
    known = list(known_positions or [None] * count)
    assigned: list[str | None] = [None] * count
    used: set[str] = set()

    for index, participant in enumerate(participants):
        spells = (
            participant.get("spell1Id"),
            participant.get("spell2Id"),
            participant.get("summoner1Id"),
            participant.get("summoner2Id"),
        )
        if SMITE_SPELL_ID in spells and "Jungle" not in used:
            assigned[index] = "Jungle"
            used.add("Jungle")

    for index, position in enumerate(known):
        if assigned[index] is None and position and position not in used:
            assigned[index] = position
            used.add(position)

    open_indices = [index for index, role in enumerate(assigned) if role is None]
    open_roles = [role for role in ROLE_ORDER if role not in used]

    if open_indices and len(open_roles) == len(open_indices):
        names = [champion_internal_ids[index] for index in open_indices]
        tags = [champion_tags.get(name, ()) for name in names]
        best = max(
            itertools.permutations(open_roles),
            key=lambda permutation: sum(
                _fit_score(role, names[slot], tags[slot])
                for slot, role in enumerate(permutation)
            ),
        )
        for slot, index in enumerate(open_indices):
            assigned[index] = best[slot]
    elif open_indices:
        for index, role in zip(open_indices, [*open_roles, *ROLE_ORDER]):
            assigned[index] = role

    LOGGER.debug("Assigned positions: %s", list(zip(champion_internal_ids, assigned)))
    return [role or "Top" for role in assigned]


def role_sort_key(role: str | None) -> int:
    """Sort key placing rows in Top/Jungle/Mid/Bottom/Support order."""
    return _ROLE_INDEX.get(role or "", len(ROLE_ORDER))

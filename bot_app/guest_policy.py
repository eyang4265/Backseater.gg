"""Identity and teammate rules for the intentionally anonymous guest account."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

CRISPY_PUUID = (
    "Ov_bKZJlUPt2r10tgceplYDLGkkJHrFSwny2bvxWAKObeWfXldPiUB4-32V1obEnf6OBPLVrAUXK0g"
)
GUEST_PUUID = (
    "WlZfmtwbX6lv__fayTxKS368SZICKMZzLL-JWeZsx8FnBGQjO7pg4_B6sBfdmdZuOMJzCt5wCii4LQ"
)
GUEST_NAME = "boba monkey ball"


def find_guest(participants: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the guest by stable PUUID, retaining the old name fallback."""
    target_name = GUEST_NAME.casefold()
    for participant in participants:
        name = (
            participant.get("riotIdGameName")
            or participant.get("riotId")
            or participant.get("summonerName")
            or ""
        )
        if (
            participant.get("puuid") == GUEST_PUUID
            or name.strip().casefold() == target_name
        ):
            return participant
    return None


def guest_is_in_game_with_crispy(participants: Iterable[dict[str, Any]]) -> bool:
    """Whether both the guest and CrispyPineapple appear in this game."""
    entries = list(participants)
    guest = find_guest(entries)
    crispy = next(
        (
            participant
            for participant in entries
            if participant.get("puuid") == CRISPY_PUUID
        ),
        None,
    )
    return guest is not None and crispy is not None

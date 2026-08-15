"""Announces matches where one specific pair of players queued together.

One tracked account plus a teammate who isn't tracked and isn't on Discord.
The guest is never shown by name, so these announcements identify everyone by
the champion they played instead of by summoner name.

Kept separate from :mod:`bot_app.tracker` — with its own state file — so it
can't disturb the ranked tracker's baselines. The formatting and publishing
themselves are shared, via :mod:`bot_app.announce`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .announce import (
    MatchAnnouncement,
    TrackedPlayer,
    format_match,
    publish,
    resolve_announcement_channel,
)
from .guest_policy import (
    CRISPY_PUUID,
    GUEST_NAME as _GUEST_NAME,
    find_guest,
    guest_is_in_game_with_crispy,
)
from .render import NameStyle
from .riot import RiotAPIError, get_client
from .store import load_guest_matches, save_guest_matches

LOGGER = logging.getLogger(__name__)

TARGET_PUUID = CRISPY_PUUID
TARGET_SERVER = "NA1"
TARGET_FALLBACK_NAME = "CrispyPineapple"

GUEST_NAME = _GUEST_NAME
GUEST_DISPLAY = "Guest"

MATCH_LOOKBACK = 20


def _find_guest(match: dict[str, Any]) -> dict[str, Any] | None:
    """The guest's participant entry in this match, or None if they weren't in it."""
    return find_guest(match.get("info", {}).get("participants", []) or [])


def build_guest_players(match: dict[str, Any]) -> list[TrackedPlayer] | None:
    """The two players to call out, or None unless both were in the game.

    Shared with ``/selftest sample:guest`` so the self-test exercises the real
    selection logic rather than a copy of it.
    """
    participants = match.get("info", {}).get("participants", []) or []
    guest = _find_guest(match)
    if guest is None or not guest_is_in_game_with_crispy(participants):
        return None

    target_name = (
        get_client().riot_id(TARGET_PUUID, TARGET_SERVER) or TARGET_FALLBACK_NAME
    )
    players = [
        TrackedPlayer(puuid=TARGET_PUUID, riot_id=target_name, server=TARGET_SERVER)
    ]

    guest_puuid = guest.get("puuid")
    if guest_puuid and guest_puuid != TARGET_PUUID:
        players.append(TrackedPlayer(puuid=guest_puuid, riot_id=GUEST_DISPLAY))
    return players


def collect_new_guest_matches() -> list[MatchAnnouncement]:
    """Check the target's newest matches for the guest. Blocking; call in a thread."""
    client = get_client()
    known = load_guest_matches()
    known_ids = set(known)

    try:
        match_ids = client.match_ids(TARGET_PUUID, TARGET_SERVER, count=MATCH_LOOKBACK)
    except RiotAPIError as error:
        LOGGER.warning("Could not fetch match ids for the guest tracker: %s", error)
        return []

    new_match_ids = [
        match_id for match_id in reversed(match_ids) if match_id not in known_ids
    ]
    if not new_match_ids:
        return []

    announcements: list[MatchAnnouncement] = []
    seen: list[str] = []

    for match_id in new_match_ids:
        try:
            match = client.match(match_id, TARGET_SERVER)
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch match %s: %s", match_id, error)
            continue
        if "info" not in match:
            continue

        seen.append(match_id)

        players = build_guest_players(match)
        if players is None:
            continue

        announcement = format_match(
            match,
            players,
            require_finished=False,
            require_ranked_queue=False,
            name_style=NameStyle.CHAMPION,
        )
        if announcement is not None:
            announcements.append(announcement)

    if seen:
        save_guest_matches([*known, *seen])

    return announcements


async def guest_poll_and_announce(bot: Any) -> None:
    """Background loop entry point."""
    channel = await resolve_announcement_channel(bot)
    if channel is None:
        LOGGER.warning(
            "Skipping guest-match collection until the fallback channel is available"
        )
        return
    await publish(
        bot,
        await asyncio.to_thread(collect_new_guest_matches),
        global_channel=channel,
    )

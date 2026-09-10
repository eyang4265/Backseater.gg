"""Independent TFT account registration and poller-state seeding."""

from __future__ import annotations

import logging
from typing import Callable

from .account_registry import DuplicateAccountError, RegistryError
from .routing import lookup_platforms, split_riot_id
from .riot import RiotAPIError, get_client
from .store import (
    Account,
    TftPlayerState,
    load_accounts,
    load_tft_accounts,
    load_tft_live_game_state,
    load_tft_tracker_state,
    save_tft_live_game_state,
    save_tft_tracker_state,
    update_tft_accounts,
)

__all__ = [
    "track_tft_account",
    "untrack_tft_account",
    "update_all_tft_from_league",
    "update_tft_from_league",
]
from .tracker import MATCH_LOOKBACK

LOGGER = logging.getLogger(__name__)


def update_all_tft_from_league(
    *,
    max_accounts: int = 25,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[tuple[Account, Account]], dict[str, str]]:
    """Refresh every TFT identity from its stored League PUUID.

    ``progress`` is invoked from this worker thread after each account with
    ``(completed, total)`` so a caller can report that a long roster refresh is
    still alive rather than sitting silent until it finishes.
    """
    league_accounts = load_accounts()
    total = len(league_accounts)
    updated: list[tuple[Account, Account]] = []
    failures: dict[str, str] = {}
    for index, discord_id in enumerate(league_accounts, start=1):
        try:
            updated.append(
                update_tft_from_league(discord_id, max_accounts=max_accounts)
            )
        except RegistryError as error:
            failures[discord_id] = str(error)
        if progress is not None:
            progress(index, total)
    return updated, failures


def update_tft_from_league(
    discord_id: int | str, *, max_accounts: int = 25
) -> tuple[Account, Account]:
    """Refresh a TFT PUUID using the Riot ID behind a stored League PUUID."""
    league_account = load_accounts().get(str(discord_id))
    if league_account is None:
        raise RegistryError("That Discord user has no tracked League account.")
    client = get_client()
    try:
        riot_id = client.riot_id(
            league_account.puuid,
            league_account.server,
            refresh=True,
        )
    except RiotAPIError as error:
        raise RegistryError(str(error)) from error
    if not riot_id:
        raise RegistryError("The stored League PUUID could not be resolved to a Riot ID.")
    name, tag = split_riot_id(riot_id, None)
    if not name or not tag:
        raise RegistryError(
            "The League PUUID did not resolve to a Riot ID in the form Name#Tag."
        )
    tft_account = track_tft_account(
        discord_id,
        name,
        tag,
        league_account.server,
        allow_reassign=True,
        max_accounts=max_accounts,
        known=load_tft_accounts().get(str(discord_id)),
    )
    return league_account, tft_account


def track_tft_account(
    discord_id: int | str,
    summoner: str,
    tag: str,
    server: str,
    *,
    allow_reassign: bool = False,
    max_accounts: int = 25,
    known: Account | None = None,
) -> Account:
    """Resolve and link a TFT-specific PUUID without touching League state.

    ``known`` is the registration this call is refreshing, if any.  When the
    resolved PUUID still matches it, the account's platform is already settled
    and its history already seeded, so both of those uncached Riot calls are
    skipped -- that is what makes a whole-roster ``/tftupdate`` affordable.
    """
    client = get_client()
    candidates = lookup_platforms(server)
    puuid = None
    try:
        for candidate in candidates:
            puuid = client.tft_puuid(summoner, tag, candidate)
            if puuid:
                break
    except RiotAPIError as error:
        raise RegistryError(str(error)) from error
    if not puuid:
        searched = ", ".join(candidates[:-1]) + f", or {candidates[-1]}"
        raise RegistryError(f"{summoner}#{tag} could not be found for TFT on {searched}.")
    refreshing = known is not None and known.puuid == puuid
    try:
        server = (
            known.server
            if refreshing
            else client.tft_home_platform(puuid, candidates) or candidates[0]
        )
        riot_id = client.tft_riot_id(puuid, server) or f"{summoner}#{tag}"
    except RiotAPIError as error:
        raise RegistryError(str(error)) from error
    if refreshing:
        # Already seeded; keep whatever the poller has remembered so far.
        match_ids = None
        initialized = True
    else:
        try:
            match_ids = client.tft_match_ids(puuid, server, count=MATCH_LOOKBACK)
            initialized = True
        except RiotAPIError as error:
            LOGGER.info("Could not seed TFT matches for %s: %s", riot_id, error)
            match_ids = []
            initialized = False

    target_id = str(discord_id)
    account = Account(target_id, puuid, server, riot_id)

    def mutate(accounts: dict[str, Account]) -> None:
        duplicate = next(
            (
                item
                for key, item in accounts.items()
                if item.puuid == puuid and key != target_id
            ),
            None,
        )
        if duplicate and not allow_reassign:
            raise DuplicateAccountError(duplicate.discord_id)
        if target_id not in accounts and len(accounts) >= max_accounts:
            raise RegistryError(
                f"The tracked TFT account limit ({max_accounts}) has been reached."
            )

        state = load_tft_tracker_state()
        live = load_tft_live_game_state()
        if match_ids is None:
            # Refreshing an identity we already track: leave the remembered
            # match ids and the live lobby alone, or the next poll would
            # re-announce games this account has already been announced for.
            state.setdefault(target_id, TftPlayerState([], initialized))
        else:
            state[target_id] = TftPlayerState(list(match_ids), initialized)
            live.pop(target_id, None)
        if duplicate:
            state.pop(duplicate.discord_id, None)
            live.pop(duplicate.discord_id, None)
            accounts.pop(duplicate.discord_id, None)
        save_tft_tracker_state(state)
        save_tft_live_game_state(live)
        accounts[target_id] = account

    update_tft_accounts(mutate)
    LOGGER.info("Tracking TFT account %s for Discord id %s", riot_id, target_id)
    return account


def untrack_tft_account(discord_id: int | str) -> Account | None:
    """Remove a TFT identity and its poller state for one Discord id."""
    target_id = str(discord_id)
    removed: Account | None = None

    def mutate(accounts: dict[str, Account]) -> None:
        nonlocal removed
        removed = accounts.get(target_id)
        if removed is None:
            return
        state = load_tft_tracker_state()
        state.pop(target_id, None)
        save_tft_tracker_state(state)
        live = load_tft_live_game_state()
        live.pop(target_id, None)
        save_tft_live_game_state(live)
        accounts.pop(target_id, None)

    update_tft_accounts(mutate)
    if removed is not None:
        LOGGER.info("Untracked TFT account %s for discord_id=%s", removed.riot_id, target_id)
    return removed

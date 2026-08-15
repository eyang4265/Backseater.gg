"""Self-service tracked-account registry operations."""

from __future__ import annotations

from .ranks import fetch_ranks
from .riot import RiotAPIError, get_client
from .store import (
    Account,
    PlayerState,
    load_live_game_state,
    load_tracker_state,
    save_live_game_state,
    save_tracker_state,
    update_accounts,
)
from .tracker import MATCH_LOOKBACK


class RegistryError(RuntimeError):
    pass


class DuplicateAccountError(RegistryError):
    def __init__(self, owner_discord_id: str) -> None:
        """Initialize the instance."""
        self.owner_discord_id = owner_discord_id
        super().__init__(
            f"That Riot account is already tracked by <@{owner_discord_id}>."
        )

    def __str__(self) -> str:
        """Return the human-readable representation."""
        return str(self.args[0])


def track_account(
    discord_id: int | str,
    summoner: str,
    tag: str,
    server: str,
    *,
    allow_reassign: bool = False,
    max_accounts: int = 25,
) -> Account:
    """Resolve, seed, and link an account without backfilling announcements."""
    client = get_client()
    puuid = client.puuid(summoner, tag, server)
    if not puuid:
        raise RegistryError(f"{summoner}#{tag} could not be found on {server}.")
    riot_id = client.riot_id(puuid, server) or f"{summoner}#{tag}"
    try:
        match_ids = client.match_ids(puuid, server, count=MATCH_LOOKBACK)
    except RiotAPIError as error:
        raise RegistryError(f"Could not seed recent matches: {error}") from error
    ranks = fetch_ranks(puuid, server)
    target_id = str(discord_id)
    account = Account(target_id, puuid, server, riot_id)

    def mutate(accounts: dict[str, Account]) -> None:
        """Apply the requested state mutation."""
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
                f"The tracked-account limit ({max_accounts}) has been reached."
            )

        state = load_tracker_state()
        seeded = PlayerState(matches=list(match_ids))
        if ranks is not None:
            seeded.ranks.update(ranks)
        state[target_id] = seeded
        if duplicate:
            state.pop(duplicate.discord_id, None)
            accounts.pop(duplicate.discord_id, None)
        save_tracker_state(state)
        live = load_live_game_state()
        live.pop(target_id, None)
        if duplicate:
            live.pop(duplicate.discord_id, None)
        save_live_game_state(live)
        accounts[target_id] = account

    update_accounts(mutate)
    return account


def untrack_account(discord_id: int | str) -> Account | None:
    """Remove an account and all poller state that belongs to its Discord id."""
    target_id = str(discord_id)
    removed: Account | None = None

    def mutate(accounts: dict[str, Account]) -> None:
        """Apply the requested state mutation."""
        nonlocal removed
        removed = accounts.get(target_id)
        if removed is None:
            return
        state = load_tracker_state()
        state.pop(target_id, None)
        save_tracker_state(state)
        live = load_live_game_state()
        live.pop(target_id, None)
        save_live_game_state(live)
        accounts.pop(target_id, None)

    update_accounts(mutate)
    return removed

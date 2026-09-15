"""Maintenance of the tracked-account roster."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from .riot import get_client
from .store import Account, load_accounts, update_accounts

LOGGER = logging.getLogger(__name__)

_REFRESH_WORKERS = 8


@dataclass
class RiotIdRefresh:
    """Outcome of re-resolving every tracked account's riot id."""

    changed: list[str] = field(default_factory=list)
    unchanged: int = 0
    failed: list[str] = field(default_factory=list)


def refresh_riot_ids() -> RiotIdRefresh:
    """Re-fetch each account's current ``Name#Tag`` and persist any changes.

    Accounts are resolved concurrently; a name change is recorded as
    ``"old -> new"``.
    """
    accounts = load_accounts()
    result = RiotIdRefresh()
    if not accounts:
        LOGGER.debug("Riot id refresh requested with no tracked accounts")
        return result

    LOGGER.info("Refreshing riot ids for %d tracked accounts", len(accounts))
    client = get_client()

    def resolve(account: Account) -> tuple[Account, str | None]:
        """Resolve resolve."""
        return account, client.riot_id(account.puuid, account.server, refresh=True)

    changed_ids: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=min(_REFRESH_WORKERS, len(accounts))) as pool:
        for account, riot_id in pool.map(resolve, accounts.values()):
            if riot_id is None:
                result.failed.append(
                    f"<@{account.discord_id}>: could not update {account.riot_id}"
                )
                LOGGER.debug("Riot id refresh failed for %s", account.riot_id)
            elif riot_id != account.riot_id:
                result.changed.append(f"{account.riot_id} -> {riot_id}")
                changed_ids[account.discord_id] = (account.puuid, riot_id)
                LOGGER.debug("Riot id changed: %s -> %s", account.riot_id, riot_id)
            else:
                result.unchanged += 1

    if result.changed:
        def apply(latest: dict[str, Account]) -> None:
            """Merge name changes without resurrecting or replacing accounts."""
            for discord_id, (puuid, riot_id) in changed_ids.items():
                current = latest.get(discord_id)
                if current is not None and current.puuid == puuid:
                    latest[discord_id] = replace(current, riot_id=riot_id)

        update_accounts(apply)
    LOGGER.info(
        "Riot id refresh complete: changed=%d unchanged=%d failed=%d",
        len(result.changed), result.unchanged, len(result.failed),
    )
    return result

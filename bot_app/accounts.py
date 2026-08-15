"""Maintenance of the tracked-account roster."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from .riot import get_client
from .store import Account, load_accounts, save_accounts

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
        return result

    client = get_client()

    def resolve(account: Account) -> tuple[Account, str | None]:
        """Resolve resolve."""
        return account, client.riot_id(account.puuid, account.server, refresh=True)

    updated: dict[str, Account] = dict(accounts)
    with ThreadPoolExecutor(max_workers=min(_REFRESH_WORKERS, len(accounts))) as pool:
        for account, riot_id in pool.map(resolve, accounts.values()):
            if riot_id is None:
                result.failed.append(
                    f"<@{account.discord_id}>: could not update {account.riot_id}"
                )
            elif riot_id != account.riot_id:
                result.changed.append(f"{account.riot_id} -> {riot_id}")
                updated[account.discord_id] = replace(account, riot_id=riot_id)
            else:
                result.unchanged += 1

    if result.changed:
        save_accounts(updated)
    return result

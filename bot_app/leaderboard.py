"""Stored-state leaderboard computation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .ranks import RankSnapshot
from .store import Account, PlayerState

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaderboardRow:
    account: Account
    rank: RankSnapshot | None


def leaderboard_rows(
    accounts: dict[str, Account],
    state: dict[str, PlayerState],
    queue_id: int,
    *,
    rank_overrides: dict[str, dict[int, RankSnapshot]] | None = None,
) -> list[LeaderboardRow]:
    """Handle rows."""
    LOGGER.debug(
        "Building leaderboard rows: %s accounts, queue_id=%s", len(accounts), queue_id
    )
    overrides = rank_overrides or {}
    empty_state = PlayerState()
    rows = [
        LeaderboardRow(
            account,
            overrides.get(discord_id, {}).get(queue_id)
            or state.get(discord_id, empty_state).ranks.get(queue_id),
        )
        for discord_id, account in accounts.items()
    ]
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            row.rank.value is not None if row.rank else False,
            row.rank.value if row.rank and row.rank.value is not None else -1,
            row.rank.games if row.rank else 0,
            row.account.riot_id.casefold(),
        ),
        reverse=True,
    )
    LOGGER.info("Built leaderboard with %s row(s) for queue_id=%s", len(sorted_rows), queue_id)
    return sorted_rows

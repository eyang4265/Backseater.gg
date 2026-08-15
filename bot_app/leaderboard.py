"""Stored-state leaderboard computation."""

from __future__ import annotations

from dataclasses import dataclass

from .ranks import RankSnapshot
from .store import Account, PlayerState


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
    rows = [
        LeaderboardRow(
            account,
            (rank_overrides or {}).get(discord_id, {}).get(queue_id)
            or state.get(discord_id, PlayerState()).ranks.get(queue_id),
        )
        for discord_id, account in accounts.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            row.rank.value is not None if row.rank else False,
            row.rank.value if row.rank and row.rank.value is not None else -1,
            row.rank.games if row.rank else 0,
            row.account.riot_id.casefold(),
        ),
        reverse=True,
    )

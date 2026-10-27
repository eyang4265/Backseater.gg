"""Shared per-user admission control for expensive champion-history scans."""

from __future__ import annotations

from functools import wraps
import math
from time import monotonic


COOLDOWN_SECONDS = 60


class HistoryScanLimiter:
    """Allow one scan at a time and one start per minute across both commands."""

    def __init__(self) -> None:
        self.active: set[int] = set()
        self.next_start: dict[int, float] = {}

    def acquire(self, user_id: int) -> str | None:
        """Reserve before any await, or return a brief busy/cooldown status."""
        now = monotonic()
        self.next_start = {
            user: deadline for user, deadline in self.next_start.items()
            if deadline > now
        }
        if user_id in self.active:
            return "Your champion-history scan is still running."
        remaining = self.next_start.get(user_id, now) - now
        if remaining > 0:
            return f"Please wait {math.ceil(remaining)} seconds before another champion-history scan."
        self.active.add(user_id)
        self.next_start[user_id] = now + COOLDOWN_SECONDS
        return None

    def release(self, user_id: int) -> None:
        """Release a finished or failed scan without resetting its cooldown."""
        self.active.discard(user_id)


_LIMITER = HistoryScanLimiter()


def limit_history_scan(callback):
    """Reject repeated calls privately and hold admission through scan completion."""
    @wraps(callback)
    async def limited(self, ctx, *args, **kwargs):
        user_id = ctx.author.id
        rejection = _LIMITER.acquire(user_id)
        if rejection is not None:
            await ctx.respond(rejection, ephemeral=True)
            return
        try:
            return await callback(self, ctx, *args, **kwargs)
        finally:
            _LIMITER.release(user_id)

    return limited

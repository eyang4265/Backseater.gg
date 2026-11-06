"""Schedule-aware throttling for all three Worlds background jobs."""

from datetime import datetime, timezone
from functools import wraps
import time
from zoneinfo import ZoneInfo

FAST_SECONDS = 60
IDLE_SECONDS = 15 * 60
LEAD_SECONDS = 30 * 60
LATE_SECONDS = 6 * 3600
FINAL_GRACE_SECONDS = 15 * 60
PACIFIC = ZoneInfo("America/Los_Angeles")


def near_series(series, timestamp):
    """Stay fast for live series and starts within 30 minutes or six hours late."""
    return series is None or any(
        item["state"] == "inProgress"
        or (item["state"] == "unstarted"
            and -LATE_SECONDS <= item["start"] - timestamp <= LEAD_SECONDS)
        for item in series
    )


def schedule_aware(poll, *, daily=False):
    """Skip idle job ticks, preserving startup, 04:00 posts and final retries.

    The minute timer only reads the shared in-memory schedule when idle. Each
    job runs at most every fifteen minutes then, waking on the first minute
    inside the lead-in window. Manual command fetching remains independent.
    Failed attempts use the same cadence, avoiding retries on every idle tick.
    """
    last_attempt = None
    active_until = 0.0
    daily_day = None

    @wraps(poll)
    async def run(bot):
        nonlocal last_attempt, active_until, daily_day
        from . import worlds

        now = datetime.now(timezone.utc)
        tick = time.monotonic()
        active = near_series(worlds._cached_series, now.timestamp())
        if active and worlds._cached_series is not None:
            active_until = tick + FINAL_GRACE_SECONDS
        interval = FAST_SECONDS if active or tick < active_until else IDLE_SECONDS
        local = now.astimezone(PACIFIC)
        daily_due = daily and local.hour >= 4 and daily_day != local.date()
        # The Discord minute timer can wake a fraction of a second early.
        threshold = interval - 1 if interval == FAST_SECONDS else interval
        if last_attempt is not None and tick - last_attempt < threshold and not daily_due:
            return
        last_attempt = tick
        await poll(bot)
        if daily_due:
            daily_day = local.date()

    return run

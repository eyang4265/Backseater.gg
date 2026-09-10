"""Background pass that closes meetups once their time has gone by.

Closing lazily — only when someone next touches the message — would never
fire for exactly the case that needs it: a meetup nobody looks at again.
This runs on the same task loop as the match and live-game pollers.
"""

from __future__ import annotations

import logging
from typing import Any

from .store import MeetupStore, get_meetup_store
from .views import close_meetup

LOGGER = logging.getLogger(__name__)


async def poll_and_close_meetups(bot: Any, store: MeetupStore | None = None) -> int:
    """Close every meetup past its grace period; return how many closed."""
    active = store or get_meetup_store()
    due = active.due_for_close()
    if not due:
        return 0
    LOGGER.debug("Closing %d meetup(s) past their grace period", len(due))
    closed = 0
    for meetup in due:
        try:
            await close_meetup(bot, meetup, active)
            closed += 1
        except Exception:
            LOGGER.exception("Could not close meetup %d", meetup.meetup_id)
    return closed

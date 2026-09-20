"""Rate-limited owner DMs for unexpected runtime failures."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterable
from datetime import datetime, timezone

import discord


LOGGER = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 15 * 60
MAX_ERROR_TEXT_LENGTH = 700
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password)(\s*[:=]\s*)([^\s&]+)"
)


def _safe_error_text(error: BaseException, secrets: Iterable[str]) -> str:
    """Return a short single-line error description with known secrets removed."""
    text = f"{type(error).__name__}: {error}".replace("\n", " ").replace("`", "'")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _SENSITIVE_ASSIGNMENT.sub(r"\1\2[redacted]", text)
    if len(text) > MAX_ERROR_TEXT_LENGTH:
        text = text[: MAX_ERROR_TEXT_LENGTH - 1] + "…"
    return text


class OwnerErrorNotifier:
    """Deliver unexpected errors to the configured owner without alert storms.

    Identical source/error pairs are suppressed for a cooldown. Notification
    failures are logged and swallowed so a Discord DM problem can never create
    a recursive application failure.
    """

    def __init__(
        self,
        bot: discord.Bot,
        owner_id: int,
        *,
        secrets: Iterable[str] = (),
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self.bot = bot
        self.owner_id = owner_id
        self.secrets = tuple(secret for secret in secrets if secret)
        self.cooldown_seconds = cooldown_seconds
        self._last_sent: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def notify(self, source: str, error: BaseException) -> bool:
        """DM one sanitized alert; return whether Discord accepted the message."""
        safe_error = _safe_error_text(error, self.secrets)
        fingerprint = f"{source}\0{safe_error}"
        now = time.monotonic()
        async with self._lock:
            last_sent = self._last_sent.get(fingerprint)
            if last_sent is not None and now - last_sent < self.cooldown_seconds:
                return False
            # Reserve the cooldown before network I/O so concurrent failures
            # cannot fan out duplicate DMs.
            self._last_sent[fingerprint] = now

        timestamp = int(datetime.now(timezone.utc).timestamp())
        content = (
            "⚠️ **VibeCode Bot error**\n"
            f"**Source:** `{source[:200]}`\n"
            f"**Error:** `{safe_error}`\n"
            f"**Time:** <t:{timestamp}:F>\n"
            "Identical alerts are suppressed for 15 minutes."
        )
        try:
            owner = self.bot.get_user(self.owner_id)
            if owner is None:
                owner = await self.bot.fetch_user(self.owner_id)
            await owner.send(content)
        except (discord.DiscordException, AttributeError):
            LOGGER.exception("Could not send owner error DM for %s", source)
            return False
        return True

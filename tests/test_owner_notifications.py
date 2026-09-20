"""Owner DM error notification behavior."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.owner_notifications import OwnerErrorNotifier, _safe_error_text


class SafeErrorTextTests(unittest.TestCase):
    def test_known_and_labeled_secrets_are_redacted(self) -> None:
        """DM content must not disclose configured or labeled credentials."""
        rendered = _safe_error_text(
            RuntimeError("token=abc123 api_key=xyz secret-value"),
            ("secret-value",),
        )
        self.assertNotIn("abc123", rendered)
        self.assertNotIn("xyz", rendered)
        self.assertNotIn("secret-value", rendered)
        self.assertIn("[redacted]", rendered)


class OwnerErrorNotifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_a_sanitized_owner_dm(self) -> None:
        """An unexpected error is delivered to the configured owner."""
        owner = Mock(send=AsyncMock())
        bot = Mock(get_user=Mock(return_value=owner), fetch_user=AsyncMock())
        notifier = OwnerErrorNotifier(bot, 42, secrets=("top-secret",))

        sent = await notifier.notify("background poller", RuntimeError("top-secret failed"))

        self.assertTrue(sent)
        bot.fetch_user.assert_not_awaited()
        content = owner.send.await_args.args[0]
        self.assertIn("background poller", content)
        self.assertNotIn("top-secret", content)

    async def test_identical_alerts_are_suppressed_during_cooldown(self) -> None:
        """Repeated poll failures must not create a DM storm."""
        owner = Mock(send=AsyncMock())
        bot = Mock(get_user=Mock(return_value=owner))
        notifier = OwnerErrorNotifier(bot, 42, cooldown_seconds=900)
        error = RuntimeError("boom")

        with patch("bot_app.owner_notifications.time.monotonic", side_effect=[10, 20]):
            self.assertTrue(await notifier.notify("poller", error))
            self.assertFalse(await notifier.notify("poller", error))
        owner.send.assert_awaited_once()

    async def test_dm_failure_is_swallowed(self) -> None:
        """A blocked DM cannot recurse into another application failure."""
        owner = Mock(send=AsyncMock(side_effect=discord.Forbidden(Mock(), "blocked")))
        bot = Mock(get_user=Mock(return_value=owner))
        notifier = OwnerErrorNotifier(bot, 42)

        with patch("bot_app.owner_notifications.LOGGER.exception") as logged:
            self.assertFalse(await notifier.notify("poller", RuntimeError("boom")))
        logged.assert_called_once()

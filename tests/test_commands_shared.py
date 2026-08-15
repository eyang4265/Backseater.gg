"""Player target resolution across stored and explicit options."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from bot_app.commands.shared import _discord_user_id, not_found_embed, resolve_target
from bot_app.store import Account


class TargetResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare fixtures for the test case."""
        self.ctx = SimpleNamespace(author=SimpleNamespace(id=1))
        self.accounts = {
            "1": Account("1", "p1", "NA1", "One#NA1"),
            "2": Account("2", "p2", "EUW1", "Two#EUW"),
        }

    def test_defaults_to_callers_stored_account(self) -> None:
        """Verify that defaults to callers stored account."""
        with patch("bot_app.commands.shared.load_accounts", return_value=self.accounts):
            target = resolve_target(self.ctx, None, None, None, include_icon=False)
        self.assertEqual(
            (target.puuid, target.server, target.riot_id), ("p1", "NA1", "One#NA1")
        )

    def test_typed_tracked_and_untracked_users(self) -> None:
        """Verify that typed tracked and untracked users."""
        with patch("bot_app.commands.shared.load_accounts", return_value=self.accounts):
            tracked = resolve_target(
                self.ctx, None, None, None, SimpleNamespace(id=2), include_icon=False
            )
            missing = resolve_target(
                self.ctx, None, None, None, SimpleNamespace(id=3), include_icon=False
            )
        self.assertEqual(tracked.puuid, "p2")
        self.assertIsNone(missing)
        self.assertIn(
            "no tracked Riot account",
            not_found_embed(None, None, None, user="3").description,
        )

    def test_combined_riot_id_and_platform_in_tag(self) -> None:
        """Verify that combined riot id and platform in tag."""
        client = Mock()
        client.puuid.return_value = "explicit"
        client.riot_id.return_value = "Name#Tag"
        with (
            patch("bot_app.commands.shared.load_accounts", return_value={}),
            patch("bot_app.commands.shared.get_client", return_value=client),
        ):
            combined = resolve_target(
                self.ctx, None, "Name#Tag", None, include_icon=False
            )
            platform_tag = resolve_target(
                self.ctx, None, "Name", "EUW1", include_icon=False
            )
        self.assertEqual(combined.puuid, "explicit")
        self.assertEqual(platform_tag.server, "EUW1")
        self.assertIn(call("Name", "EUW1", "EUW1"), client.puuid.mock_calls)

    def test_mention_parser_is_exact(self) -> None:
        """Verify that mention parser is exact."""
        self.assertEqual(_discord_user_id("<@!123>"), "123")
        self.assertEqual(_discord_user_id("123"), "123")
        self.assertIsNone(_discord_user_id("!!123"))

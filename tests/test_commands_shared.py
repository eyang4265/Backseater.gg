"""Player target resolution across stored and explicit options."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from bot_app.commands.shared import (
    _discord_user_id,
    command_option_fields,
    log_command,
    not_found_embed,
    resolve_username,
    resolve_tft_username,
    supplied_options_text,
    Target,
    target_at_latest_match_position,
    target_at_match_position,
)
from bot_app.store import Account


class TargetResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare fixtures for the test case."""
        self.ctx = SimpleNamespace(author=SimpleNamespace(id=1))
        self.accounts = {
            "1": Account("1", "p1", "NA1", "One#NA1"),
            "2": Account("2", "p2", "EUW1", "Two#EUW"),
        }
        self.teammates: dict[str, Account] = {}
        patcher = patch(
            "bot_app.commands.shared.load_teammates",
            side_effect=lambda: self.teammates,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_to_callers_stored_account(self) -> None:
        """Verify that defaults to callers stored account."""
        with patch("bot_app.commands.shared.load_accounts", return_value=self.accounts):
            target = resolve_username(self.ctx, None, None, include_icon=False)
        self.assertEqual(
            (target.puuid, target.server, target.riot_id), ("p1", "NA1", "One#NA1")
        )

    def test_tft_defaults_to_separate_tft_registry(self) -> None:
        """A TFT command never inherits the caller's League PUUID."""
        tft_accounts = {
            "1": Account("1", "tft-p1", "NA1", "TftOne#NA1")
        }
        with patch(
            "bot_app.commands.shared.load_tft_accounts", return_value=tft_accounts
        ):
            target = resolve_tft_username(self.ctx, None, None)
        self.assertEqual(target.puuid, "tft-p1")
        self.assertNotEqual(target.puuid, self.accounts["1"].puuid)

    def test_typed_tracked_and_untracked_mentions(self) -> None:
        """A mention resolves the mentioned user's tracked account, or nothing."""
        with patch("bot_app.commands.shared.load_accounts", return_value=self.accounts):
            tracked = resolve_username(self.ctx, None, "<@2>", include_icon=False)
            missing = resolve_username(self.ctx, None, "<@3>", include_icon=False)
        self.assertEqual(tracked.puuid, "p2")
        self.assertIsNone(missing)
        self.assertIn(
            "no tracked Riot account",
            not_found_embed("<@3>", None).description,
        )

    def test_mention_resolves_a_teammate_when_not_tracked(self) -> None:
        """A ``username`` mention of a teammate-only user still resolves."""
        self.teammates = {
            "tm-puuid": Account("9", "tm-puuid", "EUW1", "Mate#EUW"),
        }
        with patch("bot_app.commands.shared.load_accounts", return_value=self.accounts):
            target = resolve_username(self.ctx, None, "<@9>", include_icon=False)
        self.assertEqual(
            (target.puuid, target.server, target.riot_id),
            ("tm-puuid", "EUW1", "Mate#EUW"),
        )

    def test_default_target_falls_back_to_a_linked_teammate(self) -> None:
        """With no username and no tracked account, the caller's teammate is used."""
        self.teammates = {
            "tm-puuid": Account("1", "tm-puuid", "NA1", "Mate#NA1"),
        }
        with patch("bot_app.commands.shared.load_accounts", return_value={}):
            target = resolve_username(self.ctx, None, None, include_icon=False)
        self.assertEqual(target.puuid, "tm-puuid")

    def test_combined_riot_id_and_embedded_platform_tag(self) -> None:
        """Verify that a combined Name#Tag summoner resolves, including a stray region tag."""
        client = Mock()
        client.puuid.return_value = "explicit"
        client.home_platform.side_effect = lambda _puuid, candidates: candidates[0]
        client.riot_id.return_value = "Name#Tag"
        with (
            patch("bot_app.commands.shared.load_accounts", return_value={}),
            patch("bot_app.commands.shared.get_client", return_value=client),
        ):
            combined = resolve_username(
                self.ctx, None, "Name#Tag", include_icon=False
            )
            platform_tag = resolve_username(
                self.ctx, None, "Name#EUW1", include_icon=False
            )
        self.assertEqual(combined.puuid, "explicit")
        self.assertEqual(platform_tag.server, "EUW1")
        self.assertIn(call("Name", "EUW1", "EUW1"), client.puuid.mock_calls)

    def test_supplied_server_only_orders_the_na_euw_kr_sweep(self) -> None:
        """Verify that a named server is tried first but the others still get checked."""
        client = Mock()
        client.puuid.side_effect = lambda name, tag, server: (
            "found" if server == "KR" else None
        )
        client.home_platform.side_effect = lambda _puuid, candidates: next(
            (item for item in candidates if item == "KR"), None
        )
        client.riot_id.return_value = "Name#Tag"
        with (
            patch("bot_app.commands.shared.load_accounts", return_value={}),
            patch("bot_app.commands.shared.get_client", return_value=client),
        ):
            target = resolve_username(self.ctx, "EUW1", "Name#Tag", include_icon=False)
        self.assertEqual((target.puuid, target.server), ("found", "KR"))
        self.assertEqual(
            [item.args[2] for item in client.puuid.mock_calls],
            ["EUW1", "NA1", "KR"],
        )
        self.assertIn(
            "EUW1, NA1, or KR", not_found_embed("Name#Tag", "EUW1").description
        )

    def test_home_platform_probe_decides_the_server(self) -> None:
        """Verify the summoner probe, not the regional riot-id route, picks the server."""
        client = Mock()
        client.puuid.return_value = "found"
        client.home_platform.return_value = "EUW1"
        client.riot_id.return_value = "Name#Tag"
        with (
            patch("bot_app.commands.shared.load_accounts", return_value={}),
            patch("bot_app.commands.shared.get_client", return_value=client),
        ):
            target = resolve_username(self.ctx, None, "Name#Tag", include_icon=False)
        self.assertEqual(target.server, "EUW1")
        client.home_platform.assert_called_once_with("found", ["NA1", "EUW1", "KR"])

    def test_mention_parser_is_exact(self) -> None:
        """Verify that mention parser is exact."""
        self.assertEqual(_discord_user_id("<@!123>"), "123")
        self.assertEqual(_discord_user_id("123"), "123")
        self.assertIsNone(_discord_user_id("!!123"))

    def test_match_position_builds_the_selected_player_target(self) -> None:
        """A match slot carries the selected participant into later API calls."""
        match = {
            "info": {
                "participants": [
                    {
                        "participantId": 5,
                        "puuid": "blue-support",
                        "riotIdGameName": "Supporter",
                        "riotIdTagline": "BLUE",
                    }
                ]
            }
        }
        target = target_at_match_position(match, 5, "NA1")
        self.assertEqual(
            (target.puuid, target.server, target.riot_id),
            ("blue-support", "NA1", "Supporter#BLUE"),
        )
        self.assertIsNone(target_at_match_position(match, 6, "NA1"))

    def test_not_found_replies_echo_every_supplied_option(self) -> None:
        """A failed lookup names the platforms searched and the caller's own inputs."""
        ctx = SimpleNamespace(
            selected_options=[
                {"name": "username", "value": "Nmae#Tag"},
                {"name": "server", "value": "EUW1"},
                {"name": "match_id", "value": ""},
            ]
        )
        description = not_found_embed("Nmae#Tag", "EUW1", ctx=ctx).description
        self.assertIn("EUW1, NA1, or KR", description)
        self.assertIn("username: `Nmae#Tag`", description)
        self.assertIn("server: `EUW1`", description)
        self.assertNotIn("match_id", description)

    def test_not_found_reply_says_when_no_option_was_supplied(self) -> None:
        """Bare invocations explain that the caller's own linked account was used."""
        ctx = SimpleNamespace(selected_options=[])
        description = not_found_embed(None, None, ctx=ctx).description
        self.assertIn("no tracked Riot account", description)
        self.assertIn("defaulted to your own linked account", description)

    def test_supplied_options_text_is_empty_without_a_context(self) -> None:
        """Call sites that cannot pass a context keep the plain message."""
        self.assertEqual(supplied_options_text(None), "")

    def test_command_debugging_logs_selected_options_once(self) -> None:
        """Use the common logger for every command without duplicate lines."""
        ctx = SimpleNamespace(
            command="coachless",
            author="Tester",
            channel="#bot",
            selected_options=[{"name": "champion", "value": "Ahri"}],
        )
        self.assertEqual(command_option_fields(ctx), {"champion": "Ahri"})
        with self.assertLogs("bot_app.commands.shared", level="INFO") as logs:
            log_command(ctx, **command_option_fields(ctx))
            log_command(ctx, champion="Ahri")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("champion=Ahri", logs.output[0])


class LatestMatchPositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_position_five_resolves_blue_support_from_latest_match(self) -> None:
        """The shorthand used by /mastery follows the locator's latest match."""
        locator = Target("locator", "NA1", "Locator#NA1")
        client = Mock()
        match = {
            "info": {
                "participants": [
                    {
                        "participantId": 5,
                        "puuid": "support",
                        "riotIdGameName": "Blue Support",
                        "riotIdTagline": "NA1",
                    }
                ]
            }
        }
        threaded = AsyncMock(side_effect=[["NA1_1"], match])
        with (
            patch("bot_app.commands.shared.get_client", return_value=client),
            patch("bot_app.commands.shared.riot_to_thread", threaded),
        ):
            selected = await target_at_latest_match_position(locator, 5)

        self.assertEqual(selected.puuid, "support")
        self.assertEqual(selected.riot_id, "Blue Support#NA1")
        self.assertEqual(threaded.await_args_list[0].args[1:], ("locator", "NA1"))
        self.assertEqual(threaded.await_args_list[0].kwargs, {"count": 1})
        self.assertEqual(threaded.await_args_list[1].args[1:], ("NA1_1", "NA1"))

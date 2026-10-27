"""Owner-only account-type selection and exclusive companion moves."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.account_registry import RegistryError
from bot_app.commands import register_all
from bot_app.commands.add import (
    AddCommand, _promote_from_companion, _register_companion, _store_companion,
    _switch_to_companion,
)
from bot_app.commands.command_directory import is_owner_only, public_commands
from bot_app.store import (
    Account, load_accounts, load_partymates, load_teammates, load_tft_accounts,
    remember_partymates, save_accounts, save_tft_accounts,
)
from tests.support import temporary_state


class AddAccountTypeTests(unittest.TestCase):
    def test_owner_only_dropdown_registers_all_optional_choices(self):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            bot = discord.Bot()
            register_all(bot)
            command = next(c for c in bot.pending_application_commands if c.name == "add")
            self.assertTrue(is_owner_only(command))
            self.assertNotIn(command, public_commands(bot, tft=False))
            self.assertEqual([o.name for o in command.options], ["summoner", "user", "account_type"])
            self.assertTrue(all(not o.required for o in command.options))
            option = command.options[-1]
            self.assertEqual(option.input_type, discord.SlashCommandOptionType.string)
            self.assertEqual([c.value for c in option.choices], ["tracked", "teammate", "partymate"])
            self.assertEqual(option.default, "tracked")
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_manual_partymate_resolves_without_matches_or_tracking(self):
        client = Mock()
        client.puuid.return_value = "party"
        client.home_platform.return_value = "EUW1"
        client.riot_id.return_value = "Party#EUW"
        with temporary_state(), patch("bot_app.commands.add.get_client", return_value=client):
            account = _register_companion("42", "Party", "EUW", "partymate")
            self.assertEqual(load_partymates(), {"party": account})
            self.assertEqual((account.server, account.discord_id), ("EUW1", "42"))
            self.assertFalse(load_accounts())
            self.assertFalse(load_tft_accounts())
            self.assertFalse(load_teammates())
            client.match_ids.assert_not_called()
            client.league_entries.assert_not_called()

    def test_companion_moves_preserve_identity_and_remain_exclusive(self):
        account = Account("42", "party", "EUW1", "Party#EUW")
        with temporary_state():
            remember_partymates([account])
            self.assertEqual(_switch_to_companion("42", "teammate"), account)
            self.assertEqual(load_teammates(), {"party": account})
            self.assertFalse(load_partymates())
            self.assertEqual(_switch_to_companion("42", "partymate"), account)
            self.assertEqual(load_partymates(), {"party": account})
            self.assertFalse(load_teammates())
            loose = Account("", "party", "EUW1", "NewName#EUW")
            stored = _store_companion(loose, "partymate")
            self.assertEqual(stored.discord_id, "42")
            self.assertEqual(load_partymates()["party"].riot_id, "NewName#EUW")

    def test_tracked_switch_to_partymate_unregisters_league_and_tft(self):
        account = Account("42", "league", "NA1", "Party#NA1")
        with temporary_state():
            save_accounts({"42": account})
            save_tft_accounts({"42": Account("42", "tft", "NA1", "Party#NA1")})
            self.assertEqual(_switch_to_companion("42", "partymate"), account)
            self.assertFalse(load_accounts())
            self.assertFalse(load_tft_accounts())
            self.assertEqual(load_partymates(), {"league": account})

    def test_direct_companion_registration_cannot_overlap_tracking(self):
        account = Account("42", "party", "NA1", "Party#NA1")
        with temporary_state():
            save_accounts({"42": account})
            for kind in ("teammate", "partymate"):
                with self.assertRaisesRegex(RegistryError, "That account is tracked"):
                    _store_companion(account, kind)
            self.assertEqual(load_accounts(), {"42": account})
            self.assertFalse(load_partymates())
            self.assertFalse(load_teammates())

    def test_partymate_can_be_promoted_to_full_tracking(self):
        account = Account("42", "party", "NA1", "Party#NA1")
        tft = Account("42", "tft", "NA1", "Party#NA1")
        with temporary_state(), patch("bot_app.commands.add._track_both_accounts", return_value=(account, tft)):
            remember_partymates([account])
            self.assertEqual(_promote_from_companion("42", 25), (account, tft))
            self.assertFalse(load_partymates())

    def test_command_defers_before_companion_lookup(self):
        async def exercise():
            ctx = Mock(author=Mock(id=42), guild=Mock(id=1))
            ctx.defer = AsyncMock()
            ctx.respond = AsyncMock()
            account = Account("", "party", "NA1", "Party#NA1")

            def register(*args):
                ctx.defer.assert_awaited_once_with(ephemeral=True)
                self.assertEqual(args, ("", "Party", "NA1", "partymate"))
                return account

            with patch("bot_app.commands.add._register_companion", side_effect=register):
                await AddCommand.add.callback(AddCommand(Mock()), ctx, "Party#NA1", None, "partymate")
            self.assertIn("Added partymate", ctx.respond.call_args.kwargs["embed"].description)

        asyncio.run(exercise())

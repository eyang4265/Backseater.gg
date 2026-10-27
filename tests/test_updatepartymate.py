"""Roster backfill pagination, inference, coverage reporting, and admission."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.commands import register_all
from bot_app.commands.command_directory import is_owner_only, public_commands
from bot_app.commands.updatepartymate import UpdatePartymateCommand
from bot_app.partymate_update import PartymateUpdateResult, update_partymates
from bot_app.riot import RiotAPIError, RiotClient
from bot_app.store import (
    Account, load_partymates, remember_partymates, save_accounts, save_teammates,
)
from tests.support import temporary_state

NOW = 2_000_000_000


def match(number, *, queue=420, team=100, age=1000, remake=False, partner="party"):
    return {"metadata": {"matchId": f"NA1_{number}"}, "info": {
        "platformId": "NA1", "queueId": queue,
        "gameStartTimestamp": (NOW - age - 600) * 1000,
        "gameEndTimestamp": (NOW - age) * 1000,
        "participants": [
            {"puuid": "tracked", "teamId": 100, "riotIdGameName": "Tracked", "riotIdTagline": "NA1"},
            {"puuid": partner, "teamId": team, "riotIdGameName": "Party", "riotIdTagline": "NA1",
             "gameEndedInEarlySurrender": remake},
        ],
    }}


def live_game(team=100, start=NOW * 1000):
    info = match(3, team=team)["info"]
    return dict(info, gameQueueConfigId=420, gameStartTime=start, gameId=3)


class PartymateBackfillTests(unittest.TestCase):
    def scan(self, payloads, accounts=None, client=None):
        save_accounts(accounts or {"42": Account("42", "tracked", "NA1", "Tracked#NA1")})
        client = client or Mock()
        if not isinstance(client.match_ids.side_effect, list):
            client.match_ids.return_value = list(payloads)
        client.active_game.return_value = live_game()
        client.match.side_effect = lambda mid, _server: payloads[mid]
        with patch("bot_app.partymate_update.get_client", return_value=client):
            return update_partymates(now=NOW), client

    def test_two_completed_games_and_current_live_team_add_a_partner_and_preserve_existing_entries(self):
        with temporary_state():
            old = Account("99", "old", "EUW1", "Old#EUW")
            remember_partymates([old])
            result, client = self.scan({"NA1_2": match(2), "NA1_1": match(1, age=2000)})
            self.assertEqual(result, PartymateUpdateResult(1, 0, 2, 0, 1, 2))
            self.assertEqual(set(load_partymates()), {"old", "party"})
            self.assertEqual(load_partymates()["old"], old)
            self.assertEqual(load_partymates()["party"].riot_id, "Party#NA1")
            client.match_ids.assert_called_once_with(
                "tracked", "NA1", start=0, count=100,
                start_time=NOW - 7 * 86400, end_time=NOW,
            )

    def test_completed_history_without_current_same_team_lobby_never_admits(self):
        for live in (None, live_game(team=200), live_game(start=(NOW - 3000) * 1000)):
            with self.subTest(live=live), temporary_state():
                save_accounts({"42": Account("42", "tracked", "NA1", "Tracked#NA1")})
                client = Mock()
                client.match_ids.return_value = ["NA1_1", "NA1_2"]
                client.match.side_effect = [match(1), match(2, age=2000)]
                client.active_game.return_value = live
                with patch("bot_app.partymate_update.get_client", return_value=client):
                    self.assertEqual(update_partymates(now=NOW).added, 0)

    def test_only_one_game_or_opponent_pairs_do_not_qualify(self):
        for payloads in ({"NA1_1": match(1)}, {"NA1_1": match(1, team=200), "NA1_2": match(2, team=200)}):
            with self.subTest(payloads=payloads), temporary_state():
                result, _ = self.scan(payloads)
                self.assertEqual(result.added, 0)

    def test_other_queues_remakes_arena_and_out_of_window_games_do_not_count(self):
        for invalid in (
            match(2, queue=440), match(2, remake=True), match(2, queue=1700),
            match(2, age=8 * 86400), match(2, age=-1000),
        ):
            with self.subTest(invalid=invalid), temporary_state():
                result, _ = self.scan({"NA1_1": match(1), "NA1_2": invalid})
                self.assertEqual(result.added, 0)

    def test_frequency_guard_rejects_two_shared_games_among_ten(self):
        payloads = {f"NA1_{n}": match(n, partner="party" if n < 2 else f"solo{n}") for n in range(10)}
        with temporary_state():
            result, _ = self.scan(payloads)
            self.assertEqual(result.added, 0)

    def test_completed_mayhem_history_is_not_partymate_evidence(self):
        with temporary_state():
            result, _ = self.scan({"NA1_1": match(1, queue=2400),
                                   "NA1_2": match(2, queue=2400)})
            self.assertEqual(result.added, 0)

    def test_shared_matches_are_fetched_once_and_teammates_are_excluded(self):
        payloads = {"NA1_1": match(1), "NA1_2": match(2)}
        accounts = {"42": Account("42", "tracked", "NA1", "Tracked#NA1"),
                    "43": Account("43", "other", "NA1", "Other#NA1")}
        for payload in payloads.values():
            payload["info"]["participants"].extend([
                {"puuid": "other", "teamId": 100}, {"puuid": "mate", "teamId": 100},
            ])
        with temporary_state():
            save_teammates({"mate": Account("", "mate", "NA1", "Mate#NA1")})
            result, client = self.scan(payloads, accounts=accounts)
            self.assertEqual(client.match.call_count, 2)
            self.assertEqual(set(load_partymates()), {"party"})
            self.assertEqual(result.accounts, 2)

    def test_paginates_every_page_in_the_fixed_window(self):
        payloads = {f"NA1_{n}": match(n) for n in range(3)}
        with temporary_state(), patch("bot_app.partymate_update.PAGE_SIZE", 2):
            save_accounts({"42": Account("42", "tracked", "NA1", "Tracked#NA1")})
            client = Mock()
            client.active_game.return_value = live_game()
            client.match_ids.side_effect = [["NA1_0", "NA1_1"], ["NA1_2"]]
            client.match.side_effect = lambda mid, _server: payloads[mid]
            with patch("bot_app.partymate_update.get_client", return_value=client):
                result = update_partymates(now=NOW)
            self.assertEqual(result.matches, 3)
            self.assertEqual([c.kwargs["start"] for c in client.match_ids.call_args_list], [0, 2])
            self.assertTrue(all(c.kwargs["start_time"] == NOW - 7 * 86400 for c in client.match_ids.call_args_list))

    def test_unavailable_matches_report_incomplete_without_false_additions(self):
        with temporary_state():
            save_accounts({"42": Account("42", "tracked", "NA1", "Tracked#NA1")})
            client = Mock()
            client.active_game.return_value = live_game()
            client.match_ids.return_value = ["NA1_1", "NA1_2", "NA1_3"]
            client.match.side_effect = [match(1), match(2), RiotAPIError("HTTP 403")]
            with patch("bot_app.partymate_update.get_client", return_value=client):
                result = update_partymates(now=NOW)
            self.assertEqual((result.incomplete_accounts, result.unavailable_matches, result.added), (1, 1, 0))

    def test_one_failed_account_does_not_block_the_other_roster_members(self):
        with temporary_state():
            save_accounts({"42": Account("42", "bad", "NA1", "Bad#NA1"),
                           "43": Account("43", "tracked", "NA1", "Tracked#NA1")})
            client = Mock()
            client.active_game.return_value = live_game()
            client.match_ids.side_effect = [RiotAPIError("offline"), ["NA1_1", "NA1_2"]]
            client.match.side_effect = [match(1), match(2)]
            with patch("bot_app.partymate_update.get_client", return_value=client):
                result = update_partymates(now=NOW)
            self.assertEqual((result.accounts, result.incomplete_accounts, result.added), (2, 1, 1))

    def test_live_lookup_failure_reports_incomplete_and_does_not_admit(self):
        with temporary_state():
            save_accounts({"42": Account("42", "tracked", "NA1", "Tracked#NA1")})
            client = Mock()
            client.match_ids.return_value = ["NA1_1", "NA1_2"]
            client.match.side_effect = [match(1), match(2)]
            client.active_game.side_effect = RiotAPIError("offline")
            with patch("bot_app.partymate_update.get_client", return_value=client):
                result = update_partymates(now=NOW)
            self.assertEqual((result.incomplete_accounts, result.added), (1, 0))

    def test_riot_client_passes_optional_time_bounds_and_preserves_old_calls(self):
        client = object.__new__(RiotClient)
        client._get = Mock(return_value=[])
        client.match_ids("p", "NA1")
        self.assertEqual(client._get.call_args.kwargs["params"], {"start": 0, "count": 20})
        client.match_ids("p", "NA1", start_time=10, end_time=20)
        self.assertEqual(client._get.call_args.kwargs["params"], {"start": 0, "count": 20, "startTime": 10, "endTime": 20})

    def test_empty_roster_does_not_contact_riot_or_delete_saved_partymates(self):
        with temporary_state(), patch("bot_app.partymate_update.get_client") as client:
            remember_partymates([Account("", "old", "NA1", "Old#NA1")])
            result = update_partymates(now=NOW)
            client.assert_not_called()
            self.assertEqual(result, PartymateUpdateResult(0, 0, 0, 0, 0, 1))

    def test_repeated_full_page_stops_with_incomplete_coverage(self):
        with temporary_state(), patch("bot_app.partymate_update.PAGE_SIZE", 2):
            save_accounts({"42": Account("42", "tracked", "NA1", "Tracked#NA1")})
            client = Mock()
            client.active_game.return_value = live_game()
            client.match_ids.return_value = ["NA1_1", "NA1_2"]
            client.match.side_effect = [match(1), match(2)]
            with patch("bot_app.partymate_update.get_client", return_value=client):
                result = update_partymates(now=NOW)
            self.assertEqual(client.match_ids.call_count, 2)
            self.assertEqual((result.incomplete_accounts, result.added), (1, 0))


class UpdatePartymateCommandTests(unittest.TestCase):
    def test_registered_without_options_and_owner_only(self):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            bot = discord.Bot()
            register_all(bot)
            command = next(c for c in bot.pending_application_commands if c.name == "updatepartymate")
            self.assertTrue(is_owner_only(command))
            self.assertNotIn(command, public_commands(bot, tft=False))
            self.assertEqual(command.options, [])
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_private_acknowledgement_precedes_worker_and_guard_releases(self):
        async def exercise():
            ctx = Mock(author=Mock(id=42), guild=Mock(id=1))
            ctx.defer, ctx.edit = AsyncMock(), AsyncMock()
            cog = UpdatePartymateCommand(Mock())

            def scan(**kwargs):
                ctx.defer.assert_awaited_once_with(ephemeral=True)
                return PartymateUpdateResult(2, 0, 10, 0, 3, 5)

            with patch("bot_app.commands.updatepartymate.update_partymates", side_effect=scan):
                await UpdatePartymateCommand.updatepartymate.callback(cog, ctx)
            self.assertFalse(cog._running)
            self.assertIn("Added **3**", ctx.edit.call_args.kwargs["embed"].description)
            cog._running = True
            with patch("bot_app.commands.updatepartymate.update_partymates") as worker:
                await UpdatePartymateCommand.updatepartymate.callback(cog, ctx)
                worker.assert_not_called()
            self.assertIn("already running", ctx.edit.call_args.kwargs["embed"].description)

        asyncio.run(exercise())

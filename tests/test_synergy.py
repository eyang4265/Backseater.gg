"""Synergy aggregation, command defaults, exclusions, and Discord field limits."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot_app.commands.synergy import SynergyCommands, synergy_embed
from bot_app.synergy_records import SynergyRecord, aggregate_synergies
from tests.test_party import match


class SynergyTests(unittest.TestCase):
    def test_exact_groups_deduplicate_and_never_count_subsets(self):
        games = [match("1", "abcde"), match("2", "abcde", win=False),
                 match("2", "abcde", win=False), match("3", "abcde", queue=450)]
        rows = aggregate_synergies(games, "a", "abcde", min_games=1)
        self.assertEqual([(r.roster, r.queue_id, r.games, r.wins) for r in rows],
                         [(tuple("abcde"), 450, 1, 1), (tuple("abcde"), 440, 2, 1)])
        self.assertEqual(aggregate_synergies(games, "a", "abcde", min_games=1, size=2), [])
        self.assertEqual(aggregate_synergies(games, "a", "abcde"), [])

    def test_all_group_sizes_and_whole_group_teammate_filter(self):
        games = [match(str(n), "abcde"[:n]) for n in range(2, 6)]
        rows = aggregate_synergies(games, "a", "abcde", min_games=1)
        self.assertEqual({len(r.roster) for r in rows}, {2, 3, 4, 5})
        rows = aggregate_synergies(games, "a", "abcde", min_games=1, teammate="d")
        self.assertEqual({r.roster for r in rows}, {tuple("abcd"), tuple("abcde")})
        for n in range(2, 6):
            rows = aggregate_synergies(games, "a", "abcde", min_games=1, size=n)
            self.assertEqual([r.roster for r in rows], [tuple("abcde"[:n])])

    def test_champion_role_direction_and_filters(self):
        game = match("1", "abc")
        a, b, c = game["info"]["participants"][:3]
        a.update(championName="MonkeyKing", teamPosition="JUNGLE")
        b.update(championName="Ahri", teamPosition="MIDDLE")
        c["teamPosition"] = ""
        self.assertEqual(aggregate_synergies([game], "a", "abc", "Ranked Flex", "Roles", 1), [])
        c["teamPosition"] = "TOP"
        rows = aggregate_synergies([game], "a", "abc", "Ranked Flex", "Roles", 1)
        self.assertEqual(rows[0].picks, ("Jungle", "Mid", "Top"))
        rows = aggregate_synergies([game], "b", "abc", min_games=1, teammate="a")
        self.assertEqual(rows[0].roster, tuple("bac"))
        self.assertEqual(rows[0].picks, ("Ahri", "Wukong", "Garen"))
        self.assertEqual(aggregate_synergies([game], "a", "abc", "ARAM", min_games=1), [])
        c["championName"] = ""
        self.assertEqual(aggregate_synergies([game], "a", "abc", min_games=1), [])

    def test_distinct_lineups_are_separate_records(self):
        games = [match("1", "abc"), match("2", "abc"), match("3", "abc", queue=450)]
        games[1]["info"]["participants"][2]["championName"] = "Ahri"
        rows = aggregate_synergies(games, "a", "abc", min_games=1)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r.games == 1 for r in rows))

    def test_bad_games_opponents_and_unknowns_are_excluded(self):
        games = [match("remake", "ab", remake=True), match("arena", "ab", queue=1750),
                 match("custom", "ab", queue=0), match("ai", "ab", queue=830)]
        for key in ("unavailable", "incomplete", "duplicate", "bot", "inconsistent", "missingwin"):
            game = match(key, "ab")
            team = game["info"]["participants"]
            if key == "unavailable": game["info"]["resultUnavailable"] = True
            if key == "incomplete": del team[0]
            if key == "duplicate": team[2]["puuid"] = "b"
            if key == "bot": team[1]["bot"] = True
            if key == "inconsistent": team[1]["win"] = False
            if key == "missingwin": del team[1]["win"]
            games.append(game)
        self.assertEqual(aggregate_synergies(games, "a", "ab", min_games=1), [])
        self.assertEqual(aggregate_synergies([match("ok", "ab")], "a", {"a", "enemy"}, min_games=1), [])

    def test_ranking_and_field_limits(self):
        games = [match(str(i), "ab", win=i != 0) for i in range(4)]
        games += [match("single", "ac")]
        rows = aggregate_synergies(games, "a", "abc", min_games=1)
        self.assertEqual([r.roster for r in rows], [tuple("ac"), tuple("ab")])
        hostile = "_" * 100 + "@everyone"
        rows = [SynergyRecord(tuple("abcde"), 440, (hostile,) * 5, 3, 2)]
        embed = synergy_embed(rows, {p: hostile for p in "abcde"}, "Name", "Champions", 3)
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        self.assertEqual(len(embed.fields), 2)
        self.assertEqual(len(embed.fields[0].value.splitlines()), 5)
        self.assertIn("5 players", embed.description)
        self.assertNotIn("@everyone", embed.fields[0].value)


class SynergyCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_omits_view_for_empty_and_single_page_reports(self):
        """Exercise Pycord's view contract for zero, one, and multiple lineups."""
        for count in (0, 1, 2):
            with self.subTest(lineups=count):
                async def send(**kwargs):
                    # Pycord tests is_finished whenever a view keyword is supplied.
                    if "view" in kwargs:
                        kwargs["view"].is_finished()
                    return SimpleNamespace()
                ctx = SimpleNamespace(author=SimpleNamespace(id=1), defer=AsyncMock(),
                                      respond=AsyncMock(side_effect=send))
                target = SimpleNamespace(puuid="a", riot_id="A#NA1", server="NA1")
                rows = [SynergyRecord(tuple("abcde"), 440, (str(i),) * 5, 3, 2)
                        for i in range(count)]
                with patch("bot_app.commands.synergy.log_command"), \
                     patch("bot_app.commands.synergy.target_for", return_value=target), \
                     patch("bot_app.commands.synergy.load_accounts", return_value={}), \
                     patch("bot_app.commands.synergy.load_teammates", return_value={}), \
                     patch("bot_app.commands.synergy.load_partymates", return_value={}), \
                     patch("bot_app.commands.synergy.get_match_cache") as cache, \
                     patch("bot_app.commands.synergy.aggregate_synergies", return_value=rows):
                    cache.return_value.player_match_payloads.return_value = []
                    await SynergyCommands.synergy.callback(SynergyCommands(), ctx, size=5)
                kwargs = ctx.respond.await_args.kwargs
                self.assertEqual("view" in kwargs, count > 1)
                if count > 1:
                    self.assertEqual(kwargs["view"].max_page, 1)

    async def test_defers_before_lookup_and_reads_only_selected_player_cache(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), defer=AsyncMock(), respond=AsyncMock())
        target = SimpleNamespace(puuid="a", riot_id="Player#NA1", server="NA1")
        async def lookup(*args, **kwargs):
            ctx.defer.assert_awaited_once()
            self.assertIsNone(args[1])
            self.assertIsNone(args[2])
            return target
        with patch("bot_app.commands.synergy.log_command"), \
             patch("bot_app.commands.synergy.target_for", side_effect=lookup), \
             patch("bot_app.commands.synergy.load_accounts", return_value={}), \
             patch("bot_app.commands.synergy.load_teammates", return_value={}), \
             patch("bot_app.commands.synergy.load_partymates", return_value={}), \
             patch("bot_app.commands.synergy.get_match_cache") as cache:
            cache.return_value.player_match_payloads.return_value = []
            await SynergyCommands.synergy.callback(SynergyCommands(), ctx)
            cache.return_value.player_match_payloads.assert_called_once_with("a")
        self.assertIn("No cached groups", ctx.respond.await_args.kwargs["embed"].description)

    async def test_explicit_unregistered_companion_and_self_selection(self):
        for companion_id in ("b", "a"):
            ctx = SimpleNamespace(author=SimpleNamespace(id=1), defer=AsyncMock(), respond=AsyncMock())
            targets = [SimpleNamespace(puuid="a", riot_id="A#NA1", server="NA1"),
                       SimpleNamespace(puuid=companion_id, riot_id="B#NA1", server="NA1")]
            with patch("bot_app.commands.synergy.log_command"), \
                 patch("bot_app.commands.synergy.target_for", side_effect=targets), \
                 patch("bot_app.commands.synergy.load_accounts", return_value={}), \
                 patch("bot_app.commands.synergy.load_teammates", return_value={}), \
                 patch("bot_app.commands.synergy.load_partymates", return_value={}), \
                 patch("bot_app.commands.synergy.get_match_cache") as cache:
                cache.return_value.player_match_payloads.return_value = [match(str(i), "ab") for i in range(3)]
                await SynergyCommands.synergy.callback(SynergyCommands(), ctx, teammate="B#NA1")
            embed = ctx.respond.await_args.kwargs["embed"]
            if companion_id == "b": self.assertIn("B#NA1", embed.fields[0].value)
            else: self.assertIn("other than", embed.description)

"""Exact group membership, queue separation, and Discord-safe party output."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from bot_app.match_cache import MatchCache
from bot_app.party_records import aggregate_parties
from bot_app.commands.party import PartyCommands, party_embed


def match(match_id, roster, *, win=True, queue=440, remake=False):
    members = list(roster) + [f"random{i}" for i in range(5-len(roster))]
    return {"metadata": {"matchId": match_id}, "info": {
        "queueId": queue, "gameEndTimestamp": 100000, "gameDuration": 1800,
        "participants": [{"puuid": p, "teamId": 100, "win": win,
                          "teamPosition": "TOP", "championName": "Garen",
                          "gameEndedInEarlySurrender": remake} for p in members]
        + [{"puuid": "enemy", "teamId": 200, "win": not win}],
    }}


class PartyTests(unittest.TestCase):
    def test_exact_groups_do_not_count_subsets_or_duplicate_matches(self):
        matches = [match("1", "abc"), match("2", "abc", win=False),
                   match("3", "abcde"), match("3", "abcde"), match("4", "abd")]
        rows = aggregate_parties(matches, "a", "abcde")
        records = {r.roster: (r.games, r.wins) for r in rows}
        self.assertEqual(records, {tuple("abc"): (2, 1), tuple("abcde"): (1, 1), tuple("abd"): (1, 1)})

    def test_filters_and_exclusions(self):
        matches = [match("1", "abc"), match("2", "abc", queue=450),
                   match("3", "abcde"), match("4", "abc", remake=True),
                   match("5", "abc", queue=1750), match("6", "abc", queue=9999)]
        incomplete = match("7", "abc")
        incomplete["info"]["participants"] = incomplete["info"]["participants"][:3]
        unavailable = match("8", "abc")
        unavailable["info"]["resultUnavailable"] = True
        rows = aggregate_parties(matches + [incomplete, unavailable], "a", "abcde", "Ranked Flex", 3)
        self.assertEqual([(r.games, r.wins) for r in rows], [(1, 1)])
        self.assertEqual(len(aggregate_parties(matches, "a", "abcde")), 3)

    def test_opponents_and_unknown_players_are_not_members(self):
        payload = match("1", "ab")
        rows = aggregate_parties([payload], "a", {"a", "b", "enemy"})
        self.assertEqual(rows, [])

    def test_roles_lineups_and_field_limits(self):
        payload = match("1", "abcde")
        payload["info"]["participants"][0]["championName"] = "MonkeyKing"
        row = aggregate_parties([payload], "a", "abcde")[0]
        self.assertEqual(dict(row.lineups.most_common(1)[0][0])["a"], "Wukong")
        embed = party_embed([row], {p: "_*"*100 for p in "abcde"}, "Name")
        self.assertEqual(len(embed.fields), 3)
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        self.assertIn("Wukong", embed.fields[2].value)

    def test_cache_query_is_player_scoped_and_non_remake(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory)/"matches.sqlite")
            try:
                for payload in [match("1", "abc"), match("2", "abc", remake=True), match("3", "xyz")]:
                    cache.put(payload["metadata"]["matchId"], "NA1", payload)
                self.assertEqual([m["metadata"]["matchId"] for m in cache.player_match_payloads("a")], ["1"])
            finally:
                cache.close()


class PartyCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_defers_before_lookup_and_uses_linked_default(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), defer=AsyncMock(), respond=AsyncMock())
        target = SimpleNamespace(puuid="a", riot_id="Player#NA1", server="NA1")
        async def lookup(*args, **kwargs):
            ctx.defer.assert_awaited_once()
            self.assertIsNone(args[1])
            self.assertIsNone(args[2])
            return target
        with patch("bot_app.commands.party.log_command"), patch("bot_app.commands.party.target_for", side_effect=lookup), \
             patch("bot_app.commands.party.load_accounts", return_value={}), \
             patch("bot_app.commands.party.load_teammates", return_value={}), \
             patch("bot_app.commands.party.load_partymates", return_value={}), \
             patch("bot_app.commands.party.get_match_cache") as cache:
            cache.return_value.player_match_payloads.return_value = []
            await PartyCommands.party.callback(PartyCommands(), ctx)
        self.assertIn("No completed games", ctx.respond.await_args.kwargs["embed"].description)

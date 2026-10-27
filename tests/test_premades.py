"""Premade inference must remain evidence-based and shared across surfaces."""

import unittest
from unittest.mock import patch

from bot_app import announce
from bot_app.announcement_models import (
    TrackedPlayer, live_game_announcement_payload, live_game_announcement_from_payload,
    match_announcement_payload, match_announcement_from_payload,
)
from bot_app.match_cache import MatchCache
from bot_app.premades import remember_game_partymates
from bot_app.store import load_partymates
from tests.support import temporary_state

START = 2_000_000_000_000


def participants(team=100):
    return [
        {"puuid": "a", "riotId": "Alice#NA1", "teamId": 100, "championId": 1},
        {"puuid": "b", "riotId": "Bob#NA1", "teamId": team, "championId": 2},
    ]


def completed(end=START + 600000, team=100, remake=False):
    return {"info": {
        "queueId": 420, "platformId": "NA1", "gameStartTimestamp": end - 600000,
        "gameEndTimestamp": end, "gameDuration": 600,
        "participants": [dict(p, gameEndedInEarlySurrender=remake) for p in participants(team)],
    }}


def as_live(payload):
    info = payload["info"]
    return dict(info, gameQueueConfigId=info.get("queueId"),
                gameStartTime=info.get("gameStartTimestamp"),
                platformId=info.get("platformId") or payload.get("metadata", {}).get("matchId", "").split("_")[0])


class PremadeTests(unittest.TestCase):
    def setUp(self):
        state = temporary_state()
        self.root = state.__enter__()
        self.addCleanup(state.__exit__, None, None, None)
        self.cache = MatchCache(":memory:")
        self.addCleanup(self.cache.close)
        patcher = patch("bot_app.premades.get_match_cache", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed(self, count=3, **kwargs):
        for i in range(count):
            self.cache.put(f"NA1_{i}", "NA1", completed(START - (i + 1) * 1000000, **kwargs))

    def test_shared_formatters_register_without_a_premade_section(self):
        self.seed()
        game = {"gameQueueConfigId": 420, "gameStartTime": START, "platformId": "NA1",
                "participants": participants()}
        with patch("bot_app.announce.ddragon.catalog", return_value=None):
            live = announce.format_live_game(game, [TrackedPlayer("a", "Alice#NA1")])
            self.assertEqual(set(load_partymates()), {"a", "b"})
            match = announce.format_match(completed(), [TrackedPlayer("a", "Alice#NA1")])
        self.assertNotIn("Possible premades", live.text)
        self.assertNotIn("Possible premades", match.text)
        self.assertNotIn("shared games", live.text)
        self.assertNotIn("shared games", match.text)
        self.assertEqual(live_game_announcement_from_payload(live_game_announcement_payload(live)), live)
        self.assertEqual(match_announcement_from_payload(match_announcement_payload(match)), match)
        self.assertEqual(set(load_partymates()), {"a", "b"})
        self.assertEqual(load_partymates()["a"].riot_id, "Alice#NA1")

    def test_third_live_game_registers_after_two_completed_games(self):
        game = {"gameQueueConfigId": 420, "gameStartTime": START,
                "platformId": "NA1", "participants": participants()}
        remember_game_partymates(game, live=True)
        self.assertFalse(load_partymates())
        self.seed(1)
        remember_game_partymates(game, live=True)
        self.assertFalse(load_partymates())
        self.assertFalse(load_partymates())
        self.seed(2)
        remember_game_partymates(game, live=True)
        self.assertEqual(set(load_partymates()), {"a", "b"})

    def mayhem(self, game_id, start=START, team=100):
        return {"gameQueueConfigId": 2400, "gameStartTime": start,
                "gameId": game_id, "platformId": "NA1", "participants": participants(team)}

    def test_mayhem_third_live_lobby_counts_current_and_deduplicates(self):
        game = self.mayhem(1)
        for _ in range(3):
            remember_game_partymates(game, live=True)
        self.assertFalse(load_partymates())
        remember_game_partymates(self.mayhem(2, START + 1000000), live=True)
        self.assertFalse(load_partymates())
        with patch("bot_app.announce.ddragon.catalog", return_value=None):
            announce.format_live_game(self.mayhem(3, START + 2000000),
                                      [TrackedPlayer("a", "Alice#NA1")])
        self.assertEqual(set(load_partymates()), {"a", "b"})

    def test_mayhem_completed_payload_never_counts(self):
        for i in range(2):
            payload = completed(START - (i + 1) * 1000000)
            payload["info"]["queueId"] = 2400
            self.cache.put(f"NA1_{i}", "NA1", payload)
            remember_game_partymates(payload)
        remember_game_partymates(self.mayhem(3), live=True)
        self.assertFalse(load_partymates())
        with patch.object(self.cache, "remember_live_team_games") as record:
            payload["info"]["resultUnavailable"] = True
            remember_game_partymates(payload)
        record.assert_not_called()

    def test_mayhem_window_opponents_and_scattered_games(self):
        remember_game_partymates(self.mayhem(1, START - 8 * 86400000), live=True)
        remember_game_partymates(self.mayhem(2, START + 1000000), live=True)
        remember_game_partymates(self.mayhem(3, START - 1000000, team=200), live=True)
        remember_game_partymates(self.mayhem(4), live=True)
        self.assertFalse(load_partymates())
        for i in range(7):
            game = self.mayhem(10 + i, START + (i + 2) * 1000000)
            game["participants"] = participants()[:1] + [
                {"puuid": "c", "teamId": 200}]
            remember_game_partymates(game, live=True)
        remember_game_partymates(self.mayhem(20, START + 10000000), live=True)
        self.assertFalse(load_partymates())

    def test_mayhem_missing_identity_does_not_record(self):
        for field in ("gameId", "platformId", "gameStartTime"):
            game = self.mayhem(1)
            game.pop(field)
            with patch.object(self.cache, "remember_live_team_games") as record:
                remember_game_partymates(game, live=True)
            record.assert_not_called()

    def test_mayhem_live_history_survives_reopen_without_match_statistics(self):
        path = self.root / "live-history.sqlite"
        self.cache.close()
        self.cache = MatchCache(path)
        with patch("bot_app.premades.get_match_cache", return_value=self.cache):
            remember_game_partymates(self.mayhem(1), live=True)
        self.assertIsNone(self.cache.get("NA1_1"))
        self.assertEqual(self.cache.champion_stats("a"), [])
        self.cache.close()
        self.cache = MatchCache(path)
        self.addCleanup(self.cache.close)
        with patch("bot_app.premades.get_match_cache", return_value=self.cache):
            remember_game_partymates(self.mayhem(1), live=True)
            self.assertFalse(load_partymates())
            remember_game_partymates(self.mayhem(2, START + 1000000), live=True)
            self.assertFalse(load_partymates())
            remember_game_partymates(self.mayhem(3, START + 2000000), live=True)
        self.assertEqual(set(load_partymates()), {"a", "b"})

    def test_completed_views_never_admit_new_partymates(self):
        self.seed(3)
        with patch("bot_app.announce.ddragon.catalog", return_value=None):
            announce.format_match(completed(), [TrackedPlayer("a", "Alice#NA1")])
        self.assertFalse(load_partymates())

    def test_selected_result_never_counts_as_prior_evidence(self):
        self.seed(1)
        self.cache.put("NA1_current", "NA1", completed())
        remember_game_partymates(as_live(completed()), live=True)
        self.assertFalse(load_partymates())
        self.assertFalse(load_partymates())

    def test_match_metadata_supplies_server_and_riot_id_keeps_tag(self):
        self.seed()
        payload = completed()
        payload["info"].pop("platformId")
        payload["metadata"] = {"matchId": "EUW1_123"}
        player = payload["info"]["participants"][0]
        player.pop("riotId")
        player.update(riotIdGameName="Alice", riotIdTagline="EUW")
        remember_game_partymates(as_live(payload), live=True)
        account = load_partymates()["a"]
        self.assertEqual((account.server, account.riot_id), ("EUW1", "Alice#EUW"))

    def test_storage_failure_does_not_break_announcement(self):
        self.seed()
        with patch("bot_app.premades.remember_partymates", side_effect=OSError("read only")):
            with patch("bot_app.announce.ddragon.catalog", return_value=None):
                result = announce.format_live_game(as_live(completed()), [TrackedPlayer("a", "Alice#NA1")])
            self.assertIsNotNone(result)

    def test_opponents_in_current_game_are_not_premades(self):
        self.seed()
        remember_game_partymates(as_live(completed(team=200)), live=True)
        self.assertFalse(load_partymates())

    def test_opponent_history_and_remakes_do_not_count(self):
        self.seed(team=200)
        remember_game_partymates(as_live(completed()), live=True)
        self.assertFalse(load_partymates())
        self.seed(remake=True)
        remember_game_partymates(as_live(completed()), live=True)
        self.assertFalse(load_partymates())

    def test_old_future_and_other_queue_games_are_excluded(self):
        for i in range(3):
            self.cache.put(f"old{i}", "NA1", completed(START - 8 * 86400000 - i))
            self.cache.put(f"future{i}", "NA1", completed(START + i))
            payload = completed(START - 1000000 - i)
            payload["info"]["queueId"] = 440
            self.cache.put(f"flex{i}", "NA1", payload)
        remember_game_partymates(as_live(completed()), live=True)
        self.assertFalse(load_partymates())

    def test_scattered_shared_games_are_not_enough(self):
        self.seed()
        for i in range(7):
            payload = completed(START - (i + 1) * 50000)
            payload["info"]["participants"] = participants()[:1]
            self.cache.put(f"solo{i}", "NA1", payload)
        remember_game_partymates(as_live(completed()), live=True)
        self.assertFalse(load_partymates())

    def test_invalid_timing_arena_custom_bots_and_missing_teams_are_skipped(self):
        self.seed()
        for change in ({"gameStartTimestamp": None, "gameEndTimestamp": None},
                       {"queueId": 1750}, {"gameType": "CUSTOM_GAME"}):
            payload = completed()
            payload["info"].update(change)
            remember_game_partymates(as_live(payload), live=True)
            self.assertFalse(load_partymates())
        for change in ({"bot": True}, {"teamId": None}):
            payload = completed()
            payload["info"]["participants"][1].update(change)
            remember_game_partymates(as_live(payload), live=True)
            self.assertFalse(load_partymates())

    def test_histories_are_limited_to_ten_before_the_game(self):
        self.seed(12)
        history = self.cache.recent_team_matches("a", 420, START)
        self.assertEqual(len(history), 10)
        self.assertNotIn("NA1_11", history)

    def test_overlapping_pairs_save_identities_without_claiming_party_size(self):
        for i in range(3):
            payload = completed(START - (i + 1) * 1000000)
            payload["info"]["participants"].append(
                {"puuid": "c", "riotId": "Carol", "teamId": 100})
            self.cache.put(f"abc{i}", "NA1", payload)
        payload = completed()
        payload["info"]["participants"].append({"puuid": "c", "riotId": "Carol", "teamId": 100})
        remember_game_partymates(as_live(payload), live=True)
        self.assertEqual(set(load_partymates()), {"a", "b", "c"})

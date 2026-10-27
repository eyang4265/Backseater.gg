"""Streak thresholds, historical boundaries, and shared top-summary placement."""

import unittest
from itertools import product
from unittest.mock import Mock, patch

from bot_app import announce, streaks
from bot_app.announcement_models import (
    TrackedPlayer, live_game_announcement_payload, live_game_announcement_from_payload,
    match_announcement_payload, match_announcement_from_payload,
)
from bot_app.render import TeamColumns
from bot_app.riot import RiotAPIError, TTLCache

START = 1_800_000_000_000


def match(win, start=START, **extra):
    return {"info": {"gameStartTimestamp": start, "gameEndTimestamp": start + 1000,
                     "queueId": 420, "gameDuration": 600,
                     "participants": [{"puuid": "p", "win": win, "championId": 1,
                                       "teamId": 100, **extra}]}}


class StreakTests(unittest.TestCase):
    def setUp(self):
        cache = patch.object(streaks, '_MARKERS', TTLCache(300))
        cache.start()
        self.addCleanup(cache.stop)
        client = patch.object(streaks, 'get_client')
        self.client = client.start().return_value
        self.addCleanup(client.stop)

    def seed(self, outcomes):
        self.client.match_ids.return_value = [f'NA1_{i}' for i in range(len(outcomes))]
        games = {f'NA1_{i}': match(win, START - (i + 1) * 10000)
                 for i, win in enumerate(outcomes)}
        self.client.match.side_effect = lambda mid, server: games[mid]
        return games

    def test_three_wins_or_losses_across_queues(self):
        for win, marker in ((True, ' 🔥'), (False, ' 🥶')):
            with self.subTest(win=win), patch.object(streaks, '_MARKERS', TTLCache(300)):
                games = self.seed([win] * 3)
                games['NA1_1']['info']['queueId'] = 450
                self.assertEqual(streaks.streak_marker('p', 'NA1', START), marker)
                self.client.match_ids.assert_called_with('p', 'NA1', count=3,
                                                         end_time=START // 1000 - 1)
                self.assertEqual(streaks.streak_marker('p', 'NA1', START), marker)

    def test_short_or_interrupted_streaks_have_no_marker(self):
        for results in ([], [True], [False, False], [True, False, True], [False, True, False]):
            with self.subTest(results=results), patch.object(streaks, '_MARKERS', TTLCache(300)):
                self.seed(results)
                self.assertEqual(streaks.streak_marker('p', 'NA1', START), '')

    def test_completed_result_extends_or_breaks_streak(self):
        self.seed([True, True])
        self.assertEqual(streaks.streak_marker('p', 'NA1', START, match(True)), ' 🔥')
        self.client.match_ids.assert_called_with('p', 'NA1', count=2,
                                                 end_time=START // 1000 - 1)
        self.assertEqual(streaks.streak_marker('p', 'NA1', START, match(False)), '')

    def test_unavailable_missing_or_remake_result_cannot_qualify(self):
        for change in ('unknown', 'remake', 'unavailable', 'future', 'unfinished'):
            with self.subTest(change=change), patch.object(streaks, '_MARKERS', TTLCache(300)):
                games = self.seed([True] * 3)
                info = games['NA1_1']['info']
                if change == 'unknown':
                    del info['participants'][0]['win']
                elif change == 'remake':
                    info['participants'][0]['gameEndedInEarlySurrender'] = True
                elif change == 'unavailable':
                    info['resultUnavailable'] = True
                elif change == 'future':
                    info['gameStartTimestamp'] = START + 10000
                else:
                    del info['gameEndTimestamp']
                self.assertEqual(streaks.streak_marker('p', 'NA1', START), '')
        self.assertEqual(streaks.streak_marker('p', 'NA1', START,
                                             match(True, gameEndedInEarlySurrender=True)), '')

    def test_api_failure_and_duplicate_history_omit_marker(self):
        self.client.match_ids.side_effect = RiotAPIError('unavailable')
        self.assertEqual(streaks.streak_marker('p', 'NA1', START), '')
        with patch.object(streaks, '_MARKERS', TTLCache(300)):
            self.client.match_ids.side_effect = None
            self.client.match_ids.return_value = ['same'] * 3
            self.assertEqual(streaks.streak_marker('p', 'NA1', START), '')
        self.assertEqual(streaks.streak_marker('p', None, START), '')
        self.assertEqual(streaks.streak_marker('p', 'NA1', None), '')


class TopSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_markers_stay_in_summary_across_modes_and_persistence(self):
        """Earned pentakills coexist with streaks through display changes and restores."""
        player = TrackedPlayer('p', 'Player#NA1', 'NA1')
        game = {'gameStartTime': START, 'gameQueueConfigId': 420,
                'participants': match(True)['info']['participants']}
        with patch('bot_app.announce.ddragon.catalog', return_value=None), \
             patch('bot_app.announce.remember_game_partymates'), \
             patch('bot_app.announce.streak_marker', return_value=' 🔥'):
            completed = announce.format_match(match(True, pentaKills=1), [player])
            live = announce.format_live_game(game, [player])
        self.assertIn('🖐️ 🔥', completed.text.splitlines()[1])
        self.assertIn('🔥', live.text.splitlines()[1])
        self.assertNotIn('🖐️', live.text)
        self.assertEqual(match_announcement_from_payload(match_announcement_payload(completed)), completed)
        self.assertEqual(live_game_announcement_from_payload(live_game_announcement_payload(live)), live)
        with patch('bot_app.announce.build_match_columns', return_value=TeamColumns()), \
             patch('bot_app.announce.build_lobby_columns', return_value=TeamColumns()):
            for mode in ({}, {'show_rank_names': True}, {'show_mastery': True}):
                finished_embed, _ = await announce.build_announcement_embed(
                    completed, existing_chart_url='https://example.com/chart.png', **mode)
                live_embed = await announce.build_live_game_embed(live, **mode)
                self.assertEqual(finished_embed.description, completed.text)
                self.assertEqual(live_embed.description, live.text)

    async def test_mayhem_and_clash_live_and_completed_suppress_both_streaks(self):
        player = TrackedPlayer('p', 'Player', 'NA1')
        for queue_id, streak in product((2400, 700, 720), (' 🔥', ' 🥶')):
            with self.subTest(queue_id=queue_id, streak=streak):
                finished = match(True)
                finished['info']['queueId'] = queue_id
                game = {'gameStartTime': START, 'gameQueueConfigId': queue_id,
                        'participants': finished['info']['participants']}
                with patch('bot_app.announce.ddragon.catalog', return_value=None), \
                     patch('bot_app.announce.remember_game_partymates'), \
                     patch('bot_app.announce.streak_marker', return_value=streak) as marker:
                    completed = announce.format_match(finished, [player])
                    live = announce.format_live_game(game, [player])
                marker.assert_not_called()
                self.assertNotIn(streak, completed.text)
                self.assertNotIn(streak, live.text)

    async def test_mayhem_and_clash_unavailable_notice_removes_saved_streaks(self):
        for queue_id, streak in product((2400, 700, 720), (' 🔥', ' 🥶')):
            with self.subTest(queue_id=queue_id, streak=streak):
                finished = match(None)
                finished['info'].update(queueId=queue_id, resultUnavailable=True)
                finished['liveGameSnapshot'] = {'text': '**Live Game**\nPlayer — Annie' + streak}
                with patch('bot_app.announce.ddragon.catalog', return_value=None), \
                     patch('bot_app.announce.streak_marker') as marker:
                    announcement = announce.format_match(finished, [TrackedPlayer('p', 'Player', 'NA1')])
                marker.assert_not_called()
                self.assertIn('Player — Annie', announcement.text)
                self.assertNotIn(streak, announcement.text)
                self.assertNotIn('Victory', announcement.text)


class PromotionTests(unittest.TestCase):
    def test_only_attributable_division_or_tier_increases_qualify(self):
        from bot_app.ranks import RankSnapshot, is_promotion
        before = RankSnapshot('GOLD', 'II', 90, wins=10, losses=5)
        for after, expected in (
            (RankSnapshot('GOLD', 'I', 10, wins=11, losses=5), True),
            (RankSnapshot('PLATINUM', 'IV', 10, wins=11, losses=5), True),
            (RankSnapshot('GOLD', 'II', 99, wins=11, losses=5), False),
            (RankSnapshot('GOLD', 'III', 75, wins=10, losses=6), False),
            (RankSnapshot('GOLD', 'I', 10, wins=12, losses=5), False),
            (RankSnapshot('GOLD', 'I', 10, wins=0, losses=0), False),
            (RankSnapshot('UNKNOWN', 'I', 10, wins=11, losses=5), False),
            (None, False),
        ):
            with self.subTest(after=after):
                self.assertEqual(is_promotion(before, after), expected)
        self.assertFalse(is_promotion(RankSnapshot(), before))
        self.assertTrue(is_promotion(RankSnapshot('MASTER', '', 300, wins=10),
                                     RankSnapshot('GRANDMASTER', '', 320, wins=11)))

    def test_promotion_overrides_both_streaks_in_top_summary(self):
        from bot_app.ranks import RankSnapshot
        from tests.support import temporary_state
        before = RankSnapshot('GOLD', 'II', 90, wins=10)
        after = RankSnapshot('GOLD', 'I', 10, wins=11)
        with temporary_state(), patch('bot_app.announce.ddragon.catalog', return_value=None), \
             patch('bot_app.announce.remember_game_partymates'):
            for streak in (' 🔥', ' 🥶'):
                with patch('bot_app.announce.streak_marker', return_value=streak) as marker:
                    announcement = announce.format_match(match(True), [
                        TrackedPlayer('p', 'Player#NA1', 'NA1', rank=after, previous_rank=before)])
                    self.assertIn('🎉', announcement.text.splitlines()[1])
                    self.assertNotIn(streak, announcement.text)
                    marker.assert_not_called()
            with patch('bot_app.announce.streak_marker', return_value=' 🔥'):
                after = RankSnapshot('GOLD', 'II', 99, wins=11)
                announcement = announce.format_match(match(True), [
                    TrackedPlayer('p', 'Player#NA1', 'NA1', rank=after, previous_rank=before)])
                self.assertIn('🔥', announcement.text)
                self.assertNotIn('🎉', announcement.text)

    def test_saved_match_promotion_is_available_to_command_formatter(self):
        from bot_app.ranks import RankSnapshot
        from bot_app.store import Account, PlayerState, save_accounts, save_tracker_state
        from tests.support import temporary_state
        before = RankSnapshot('GOLD', 'II', 90, wins=10)
        after = RankSnapshot('GOLD', 'I', 10, wins=11)
        for teammate in (False, True):
            with self.subTest(teammate=teammate), temporary_state():
                if not teammate:
                    save_accounts({'1': Account('1', 'p', 'NA1', 'Player#NA1')})
                state = PlayerState(ranks={420: before})
                state.record_rank(420, after, match_id='NA1_1', won=True)
                self.assertTrue(state.history[420][-1]['promotion'])
                save_tracker_state({'teammate:p' if teammate else '1': state})
                finished = match(True)
                finished['metadata'] = {'matchId': 'NA1_1'}
                with patch('bot_app.announce.ddragon.catalog', return_value=None), \
                     patch('bot_app.announce.remember_game_partymates'), \
                     patch('bot_app.announce.streak_marker', return_value='') as marker:
                    announcement = announce.format_match(finished, [TrackedPlayer('p', 'Player#NA1', 'NA1')])
                    self.assertIn('🎉', announcement.text)
                    marker.assert_not_called()
                    finished['metadata']['matchId'] = 'NA1_older'
                    older = announce.format_match(finished, [TrackedPlayer('p', 'Player#NA1', 'NA1')])
                    self.assertNotIn('🎉', older.text)

    def test_only_attributable_division_or_tier_drops_qualify(self):
        from bot_app.ranks import RankSnapshot, is_demotion
        before = RankSnapshot('GOLD', 'II', 0, wins=10, losses=5)
        for after, expected in (
            (RankSnapshot('GOLD', 'III', 75, wins=10, losses=6), True),
            (RankSnapshot('SILVER', 'I', 75, wins=10, losses=6), True),
            (RankSnapshot('GOLD', 'II', 0, wins=10, losses=6), False),
            (RankSnapshot('GOLD', 'I', 10, wins=11, losses=5), False),
            (RankSnapshot('GOLD', 'III', 75, wins=10, losses=7), False),
            (RankSnapshot('GOLD', 'III', 75, wins=0, losses=0), False),
            (RankSnapshot(), False),
            (None, False),
        ):
            with self.subTest(after=after):
                self.assertEqual(is_demotion(before, after), expected)
        self.assertTrue(is_demotion(RankSnapshot('GRANDMASTER', '', 500, wins=10, losses=5),
                                    RankSnapshot('MASTER', '', 480, wins=10, losses=6)))

    def test_demotion_overrides_streaks_and_persists_for_historical_match(self):
        from bot_app.ranks import RankSnapshot
        from bot_app.store import Account, PlayerState, save_accounts, save_tracker_state
        from tests.support import temporary_state
        before = RankSnapshot('GOLD', 'II', 0, wins=10, losses=5)
        after = RankSnapshot('GOLD', 'III', 75, wins=10, losses=6)
        for teammate in (False, True):
            with self.subTest(teammate=teammate), temporary_state(), \
                 patch('bot_app.announce.ddragon.catalog', return_value=None), \
                 patch('bot_app.announce.remember_game_partymates'):
                if not teammate:
                    save_accounts({'1': Account('1', 'p', 'NA1', 'Player#NA1')})
                for streak in (' 🔥', ' 🥶'):
                    with patch('bot_app.announce.streak_marker', return_value=streak) as marker:
                        announcement = announce.format_match(match(False), [
                            TrackedPlayer('p', 'Player#NA1', 'NA1', rank=after, previous_rank=before)])
                        self.assertIn('📉', announcement.text.splitlines()[1])
                        self.assertNotIn('🎉', announcement.text)
                        self.assertNotIn(streak, announcement.text)
                        marker.assert_not_called()
                state = PlayerState(ranks={420: before})
                state.record_rank(420, after, match_id='NA1_1', won=False)
                self.assertTrue(state.history[420][-1]['demotion'])
                self.assertFalse(state.history[420][-1]['promotion'])
                save_tracker_state({'teammate:p' if teammate else '1': state})
                finished = match(False)
                finished['metadata'] = {'matchId': 'NA1_1'}
                with patch('bot_app.announce.streak_marker', return_value=' 🥶') as marker:
                    announcement = announce.format_match(finished, [TrackedPlayer('p', 'Player#NA1', 'NA1')])
                    self.assertIn('📉', announcement.text)
                    marker.assert_not_called()
                    self.assertEqual(match_announcement_from_payload(match_announcement_payload(announcement)), announcement)
                    finished['metadata']['matchId'] = 'NA1_older'
                    older = announce.format_match(finished, [TrackedPlayer('p', 'Player#NA1', 'NA1')])
                    self.assertNotIn('📉', older.text)
                    self.assertIn('🥶', older.text)

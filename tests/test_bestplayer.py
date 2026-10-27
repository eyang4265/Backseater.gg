"""Champion leaderboard thresholds, exclusions, ordering, and acknowledgement."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot_app.best_player import aggregate_best_players, BestPlayerRow, QUEUE_CHOICES
from bot_app.commands.bestplayer import BestPlayerCommands, bestplayer_embed
from bot_app.best_player_history import BestPlayerHistory, fetch_best_players
from bot_app.riot import RiotAPIError


def game(index, puuid='a', champion='Garen', win=True, **extra):
    return {'metadata': {'matchId': str(index)}, 'info': {
        'gameStartTimestamp': 2000000000 * 1000,
        'participants': [{'puuid': puuid, 'championName': champion, 'win': win}], **extra}}


class BestPlayerTests(unittest.TestCase):
    def test_threshold_and_ranking_all_pairs(self):
        matches = [game(i) for i in range(9)]
        self.assertEqual(aggregate_best_players(matches, ['a']), [])
        matches += [game(9)]
        self.assertEqual(aggregate_best_players(matches, ['a']), [BestPlayerRow('a', 'Garen', 10, 10)])
        matches += [game(10)]
        matches += [game(100+i, 'b', win=i < 11) for i in range(12)]
        matches += [game(200+i, 'a', 'Ahri') for i in range(12)]
        rows = aggregate_best_players(matches, ['a', 'b'])
        self.assertEqual([(r.puuid, r.champion, r.games, r.wins) for r in rows],
                         [('a', 'Ahri', 12, 12), ('a', 'Garen', 11, 11), ('b', 'Garen', 12, 11)])

    def test_custom_minimum_is_inclusive(self):
        matches = [game(i) for i in range(20)]
        self.assertEqual(aggregate_best_players(matches, ['a'], 21), [])
        self.assertEqual(aggregate_best_players(matches, ['a'], 20), [BestPlayerRow('a', 'Garen', 20, 20)])
        self.assertEqual(aggregate_best_players(matches[:1], ['a'], 1), [BestPlayerRow('a', 'Garen', 1, 1)])
        embed = bestplayer_embed([], {}, min_games=20)
        self.assertIn('20+ games', embed.description)
        self.assertIn('at least 20', embed.description)

    def test_queue_filter_applies_before_threshold_and_win_rate(self):
        self.assertLessEqual(len(QUEUE_CHOICES), 25)
        self.assertIn('ARAM: Mayhem', QUEUE_CHOICES)
        matches = [game(i, queueId=420) for i in range(10)]
        matches += [game(100+i, queueId=440, win=False) for i in range(5)]
        matches += [game(200+i, queueId=450, win=False) for i in range(5)]
        self.assertEqual(aggregate_best_players(matches, ['a']), [BestPlayerRow('a', 'Garen', 20, 10)])
        self.assertEqual(aggregate_best_players(matches, ['a'], queue='Ranked'), [BestPlayerRow('a', 'Garen', 15, 10)])
        self.assertEqual(aggregate_best_players(matches, ['a'], queue='Ranked Solo/Duo'), [BestPlayerRow('a', 'Garen', 10, 10)])
        self.assertEqual(aggregate_best_players(matches, ['a'], queue='ARAM'), [])
        self.assertEqual(aggregate_best_players(matches, ['a'], 5, 'ARAM'), [BestPlayerRow('a', 'Garen', 5, 0)])

    def test_duplicates_remakes_unavailable_missing_results_and_untracked(self):
        valid = [game(i, champion='MonkeyKing' if i < 6 else 'Wukong') for i in range(11)]
        invalid = [game(20, resultUnavailable=True), game(21, win=None), game(22, puuid='other')]
        remake = game(23)
        remake['info']['participants'].append({'gameEndedInEarlySurrender': True})
        rows = aggregate_best_players(valid + valid + invalid + [remake], ['a', 'a'])
        self.assertEqual(rows, [BestPlayerRow('a', 'Wukong', 11, 11)])

    def test_embed_bounds_ranks_and_empty(self):
        rows = [BestPlayerRow(str(i), '_*'*100, 11, 10) for i in range(10)]
        embed = bestplayer_embed(rows, {str(i): '_*'*100 for i in range(10)}, 2, 4)
        self.assertEqual(len(embed.fields), 3)
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        self.assertTrue(embed.fields[0].value.startswith('#21 '))
        self.assertEqual([len(field.value.splitlines()) for field in embed.fields], [10, 10, 10])
        for line in embed.fields[0].value.splitlines():
            self.assertNotRegex(line, r'^\d+\.\s')
        self.assertIn('90.9%', embed.fields[2].value)
        self.assertIn('11 games', embed.fields[2].value)
        self.assertIn('No tracked player', bestplayer_embed([], {}).description)


class BestPlayerHistoryTests(unittest.TestCase):
    def scan(self, accounts, cached, client):
        with patch('bot_app.best_player_history.load_accounts', return_value=accounts), \
             patch('bot_app.best_player_history.get_client', return_value=client), \
             patch('bot_app.best_player_history.get_match_cache') as cache, \
             patch('bot_app.best_player_history.PAGE_SIZE', 2), \
             patch('bot_app.best_player_history.time.time', return_value=2000000000):
            cache.return_value.iter_match_payloads.return_value = iter(cached)
            return fetch_best_players(1)

    def test_window_filters_cached_and_fetched_matches_including_boundaries(self):
        end_ms = 2000000000 * 1000
        start_ms = end_ms - 180 * 86400 * 1000
        account = SimpleNamespace(puuid='a', riot_id='A', server='NA1')
        cached = [game(0, gameStartTimestamp=start_ms - 1),
                  game(1, gameStartTimestamp=start_ms),
                  game(2, gameStartTimestamp=end_ms + 1),
                  game(3, gameStartTimestamp=None)]
        client = Mock()
        client.match_ids.side_effect = [['4', '5'], ['6', '7'], ['8']]
        payloads = {'4': game(4, gameStartTimestamp=start_ms - 1),
                    '5': game(5, gameStartTimestamp=start_ms),
                    '6': game(6, gameStartTimestamp=end_ms),
                    '7': game(7, gameStartTimestamp=end_ms + 1),
                    '8': game(8, gameStartTimestamp=None, gameCreation=start_ms)}
        client.match.side_effect = lambda mid, server: payloads[mid]
        result = self.scan({'a': account}, cached, client)
        self.assertEqual(result.matches, 4)
        self.assertEqual(result.rows, [BestPlayerRow('a', 'Garen', 4, 4)])
        self.assertIn('Last 180 days', bestplayer_embed(result.rows, result.names).description)

    def test_pages_to_exhaustion_deduplicates_shared_matches_and_accounts(self):
        a = SimpleNamespace(puuid='a', riot_id='A', server='NA1')
        b = SimpleNamespace(puuid='b', riot_id='B', server='EUW1')
        client = Mock()
        client.match_ids.side_effect = [['0', '1'], ['2', '3'], [], ['1', '4'], []]
        client.match.side_effect = lambda mid, server: game(int(mid), puuid='b' if mid == '4' else 'a')
        result = self.scan({'a': a, 'duplicate': a, 'b': b}, [game(0)], client)
        self.assertEqual(result.accounts, 2)
        self.assertEqual(result.matches, 5)
        self.assertEqual(result.incomplete_accounts, 0)
        self.assertEqual(client.match.call_count, 4)
        self.assertEqual([call.kwargs['start'] for call in client.match_ids.call_args_list], [0, 2, 4, 0, 2])
        self.assertEqual(len({call.kwargs['end_time'] for call in client.match_ids.call_args_list}), 1)
        self.assertTrue(all(call.kwargs['start_time'] == 2000000000 - 180 * 86400
                            for call in client.match_ids.call_args_list))
        self.assertEqual([(r.puuid, r.games) for r in result.rows], [('a', 4), ('b', 1)])

    def test_failed_history_keeps_cached_results_and_reports_partial(self):
        account = SimpleNamespace(puuid='a', riot_id='A', server='NA1')
        client = Mock()
        client.match_ids.side_effect = RiotAPIError('unavailable')
        result = self.scan({'a': account}, [game(0)], client)
        self.assertEqual(result.rows, [BestPlayerRow('a', 'Garen', 1, 1)])
        self.assertEqual(result.incomplete_accounts, 1)
        self.assertIn('Partial history', bestplayer_embed(result.rows, result.names, coverage=result).description)

    def test_unavailable_match_and_repeated_page_are_reported(self):
        account = SimpleNamespace(puuid='a', riot_id='A', server='NA1')
        client = Mock()
        client.match_ids.side_effect = [['0', '1'], ['0', '1']]
        client.match.side_effect = [game(0), RiotAPIError('missing')]
        result = self.scan({'a': account}, [], client)
        self.assertEqual(result.unavailable_matches, 1)
        self.assertEqual(result.incomplete_accounts, 1)
        self.assertEqual(result.matches, 1)

    def test_empty_registry_skips_riot(self):
        client = Mock()
        result = self.scan({}, [], client)
        self.assertEqual(result.accounts, 0)
        self.assertEqual(result.rows, [])
        client.match_ids.assert_not_called()


class BestPlayerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def run_command(self, min_games=10, queue='All Games'):
        message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(get_partial_message=Mock(return_value=message))
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), defer=AsyncMock(),
            respond=AsyncMock(return_value=SimpleNamespace(id=123, channel=channel)))
        result = BestPlayerHistory([BestPlayerRow('a', f'Champion{i}', 20, 15) for i in range(11)], {'a': 'Player'}, 1)
        def scan(minimum, scope, *, progress):
            ctx.defer.assert_awaited_once()
            ctx.respond.assert_awaited_once()
            self.assertEqual((minimum, scope), (min_games, queue))
            progress(1, 1, 11)
            return result
        cog = BestPlayerCommands()
        with patch('bot_app.commands.bestplayer.log_command'), \
             patch('bot_app.commands.bestplayer.fetch_best_players', side_effect=scan):
            await BestPlayerCommands.bestplayer.callback(cog, ctx, min_games=min_games, queue=queue)
        self.assertFalse(cog._running)
        channel.get_partial_message.assert_called_once_with(123)
        view = message.edit.await_args.kwargs['view']
        self.assertEqual(view.max_page, 1)
        self.assertIn(f'{min_games}+ games', view.render().description)
        self.assertIn(queue, view.render().description)
        view.page = 1
        self.assertIn(f'{min_games}+ games', view.render().description)
        view.stop()

    async def test_default_scan_acknowledges_and_uses_channel_message(self):
        await self.run_command()

    async def test_custom_filters_reach_scan_and_every_page(self):
        await self.run_command(20, 'Ranked Solo/Duo')

    async def test_running_scan_rejected_without_starting_second_worker(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), respond=AsyncMock())
        cog = BestPlayerCommands()
        cog._running = True
        with patch('bot_app.commands.bestplayer.log_command'), \
             patch('bot_app.commands.bestplayer.fetch_best_players') as scan:
            await BestPlayerCommands.bestplayer.callback(cog, ctx)
        scan.assert_not_called()
        self.assertTrue(ctx.respond.await_args.kwargs['ephemeral'])

    async def test_failure_reports_error_and_releases_guard(self):
        message = SimpleNamespace(edit=AsyncMock())
        reply = SimpleNamespace(id=123, channel=SimpleNamespace(get_partial_message=Mock(return_value=message)))
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), defer=AsyncMock(), respond=AsyncMock(return_value=reply))
        cog = BestPlayerCommands()
        with patch('bot_app.commands.bestplayer.log_command'), \
             patch('bot_app.commands.bestplayer.fetch_best_players', side_effect=RuntimeError('failed')):
            await BestPlayerCommands.bestplayer.callback(cog, ctx)
        self.assertFalse(cog._running)
        self.assertIn('scan failed', message.edit.await_args.kwargs['embed'].description)

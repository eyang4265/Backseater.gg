"""Ensure repeated champion reports cannot start overlapping history work."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot_app.commands import history_scan_limit
from bot_app.commands.championpool import ChampionPoolCommands
from bot_app.commands.champstats import ChampStatsCommands


def context(user_id=1):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id), defer=AsyncMock(), respond=AsyncMock(),
    )


class HistoryScanLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.limiter = history_scan_limit.HistoryScanLimiter()
        self.guard_patch = patch.object(history_scan_limit, "_LIMITER", self.limiter)
        self.guard_patch.start()
        self.addCleanup(self.guard_patch.stop)

    async def test_cooldown_shared_between_actual_commands_before_lookup(self):
        first, second = context(), context()
        with patch("bot_app.commands.championpool.log_command"), patch(
            "bot_app.commands.championpool.target_for", new=AsyncMock(return_value=None)
        ) as pool_lookup, patch("bot_app.commands.championpool.not_found_embed"), patch(
            "bot_app.commands.champstats.target_for", new=AsyncMock()
        ) as stats_lookup:
            await ChampionPoolCommands.championpool.callback(MagicMock(), first)
            await ChampStatsCommands.champstats.callback(
                MagicMock(), second, "Garen", None, None, None, False, None, None, True, None,
            )
        pool_lookup.assert_awaited_once()
        stats_lookup.assert_not_awaited()
        second.defer.assert_not_awaited()
        self.assertTrue(second.respond.call_args.kwargs["ephemeral"])
        self.assertIn("seconds", second.respond.call_args.args[0])

    async def test_stats_scan_remains_blocked_after_minute_until_finished(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def scan(*args):
            started.set()
            await finish.wait()

        target = SimpleNamespace(puuid="p", server="NA", riot_id="Player#NA")
        cog = ChampStatsCommands(MagicMock())
        with patch("bot_app.commands.champstats.log_command"), patch(
            "bot_app.commands.champstats.target_for", new=AsyncMock(return_value=target)
        ), patch("bot_app.commands.champstats.ddragon.catalog") as catalog, patch.object(
            cog, "_finish_scan", side_effect=scan
        ), patch.object(history_scan_limit, "monotonic", return_value=100) as now:
            catalog.return_value.by_query.return_value = SimpleNamespace(name="Garen")
            task = asyncio.create_task(ChampStatsCommands.champstats.callback(
                cog, context(), "Garen", None, None, None, False, None, None, True, None,
            ))
            try:
                await asyncio.wait_for(started.wait(), 2)
                now.return_value = 161
                blocked = context()
                await ChampionPoolCommands.championpool.callback(MagicMock(), blocked)
                self.assertIn("still running", blocked.respond.call_args.args[0])
                self.assertTrue(blocked.respond.call_args.kwargs["ephemeral"])
                self.assertIsNone(self.limiter.acquire(2))
            finally:
                finish.set()
                await task
            self.assertIsNone(self.limiter.acquire(1))

    async def test_failure_releases_slot_but_retains_cooldown(self):
        @history_scan_limit.limit_history_scan
        async def fail(self, ctx):
            raise RuntimeError("scan failed")

        with patch.object(history_scan_limit, "monotonic", return_value=100) as now:
            with self.assertRaises(RuntimeError):
                await fail(None, context())
            self.assertNotIn(1, self.limiter.active)
            self.assertIn("60 seconds", self.limiter.acquire(1))
            now.return_value = 160
            self.assertIsNone(self.limiter.acquire(1))

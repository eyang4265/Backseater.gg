"""Offline timing checks for adaptive Worlds background work."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from bot_app import worlds, worlds_polling as polling


class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        self.series = [{"state": "unstarted", "start": self.now.timestamp() + 86400}]
        self.job = AsyncMock(__name__="worlds_job")
        self.run = polling.schedule_aware(self.job)
        for p in (patch.object(worlds, "_cached_series", self.series),
                  patch.object(polling, "datetime"),
                  patch.object(polling, "time")):
            p.start()
            self.addCleanup(p.stop)

    async def tick(self, seconds, run=None):
        polling.datetime.now.return_value = self.now + timedelta(seconds=seconds)
        polling.time.monotonic.return_value = 10000 + seconds
        await (run or self.run)(None)

    async def test_idle_skips_network_jobs_until_fifteen_minutes(self):
        await self.tick(0)
        await self.tick(60)
        await self.tick(899)
        self.job.assert_awaited_once()
        await self.tick(900)
        self.assertEqual(self.job.await_count, 2)

    async def test_wakes_at_thirty_minute_boundary_before_idle_deadline(self):
        self.series[0]["start"] = self.now.timestamp() + 1860
        await self.tick(0)
        await self.tick(60)
        await self.tick(120)
        self.assertEqual(self.job.await_count, 3)

    async def test_live_series_stays_fast_past_scheduled_window(self):
        self.series[0].update(state="inProgress", start=self.now.timestamp() - 86400)
        await self.tick(0)
        await self.tick(59.99)
        self.assertEqual(self.job.await_count, 2)

    async def test_final_cleanup_grace_then_returns_to_idle(self):
        self.series[0]["state"] = "inProgress"
        await self.tick(0)
        self.series[0]["state"] = "completed"
        await self.tick(60)
        await self.tick(840)
        self.assertEqual(self.job.await_count, 3)
        await self.tick(900)
        self.assertEqual(self.job.await_count, 3)
        await self.tick(1740)
        self.assertEqual(self.job.await_count, 4)

    async def test_overdue_start_stays_fast_but_stale_unstarted_does_not(self):
        self.assertTrue(polling.near_series([{"state": "unstarted", "start": 0}], 21600))
        self.assertFalse(polling.near_series([{"state": "unstarted", "start": 0}], 21601))

    async def test_four_am_post_is_not_delayed_by_idle_interval(self):
        self.now = datetime(2026, 11, 1, 11, 59, tzinfo=timezone.utc)  # 03:59 PST
        run = polling.schedule_aware(self.job, daily=True)
        await self.tick(0, run)
        await self.tick(60, run)
        await self.tick(120, run)
        self.assertEqual(self.job.await_count, 2)

    async def test_failed_idle_request_keeps_backoff(self):
        self.job.side_effect = RuntimeError("timeout")
        with self.assertRaises(RuntimeError):
            await self.tick(0)
        await self.tick(60)
        self.job.assert_awaited_once()

    async def test_startup_without_a_schedule_retries_each_minute(self):
        with patch.object(worlds, "_cached_series", None):
            await self.tick(0)
            await self.tick(60)
        self.assertEqual(self.job.await_count, 2)

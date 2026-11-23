import asyncio
import unittest
from contextlib import asynccontextmanager

from fastapi import HTTPException
from app import report_limit as limiter

slot = asynccontextmanager(limiter.report_slot)


class ReportLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = (
            limiter._slots,
            limiter._waiting,
            limiter.MAX_WAITING,
            limiter.WAIT_SECONDS,
        )
        limiter._slots = asyncio.Semaphore(2)
        limiter._waiting = 0
        limiter.MAX_WAITING = 80
        limiter.WAIT_SECONDS = 1

    async def asyncTearDown(self):
        (
            limiter._slots,
            limiter._waiting,
            limiter.MAX_WAITING,
            limiter.WAIT_SECONDS,
        ) = self.original

    async def test_at_most_two_reports_execute(self):
        active = 0
        peak = 0
        completed = 0

        async def report():
            nonlocal active, peak, completed
            async with slot(1):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
                completed += 1

        await asyncio.gather(*(report() for _ in range(12)))
        self.assertEqual(peak, 2)
        self.assertEqual(completed, 12)
        self.assertEqual(limiter._waiting, 0)

    async def test_small_customer_bypasses_busy_whale_slots(self):
        async with slot(1), slot(1):
            async def small_request():
                async with slot(2):
                    return "completed"

            result = await asyncio.wait_for(small_request(), 0.5)
            self.assertEqual(result, "completed")

    async def test_failed_report_releases_slot(self):
        with self.assertRaises(ValueError):
            async with slot(1):
                raise ValueError("Simulated report failure")

        # Both slots must be available again.
        async with slot(1), slot(1):
            pass

    async def test_queue_timeout_returns_503(self):
        limiter.WAIT_SECONDS = 0.01
        async with slot(1), slot(1):
            with self.assertRaises(HTTPException) as caught:
                async with slot(1):
                    self.fail("A third report should not start")

            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(limiter._waiting, 0)
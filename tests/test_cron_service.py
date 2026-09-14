from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from core.clock import SHANGHAI, now_shanghai
from core.cron.service import CronService
from core.cron.types import MAX_CONSECUTIVE_FAILURES, utc_iso
from core.database import InMemoryDatabase
from utils.log import ChatbotLogger


class TestCronService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = InMemoryDatabase()
        self.svc = CronService(self.db, ChatbotLogger())
        self.fired: list[str] = []

        async def runner(job):
            self.fired.append(job.id)
            return f"ran {job.prompt}"

        self.svc.attach_runner(runner)

    async def test_add_list_get_remove(self):
        frozen = datetime(2026, 9, 14, 8, 0, tzinfo=SHANGHAI)
        job = await self.svc.add(
            kind="at",
            schedule="20m",
            prompt="喝水",
            platform="admin",
            channel_type="console",
            chat_id="admin",
            user_id="admin",
            now=frozen,
        )
        self.assertEqual(job.kind, "at")
        self.assertTrue(job.delete_after_run)
        listed = await self.svc.list_jobs(user_id="admin", platform="admin")
        self.assertEqual(len(listed), 1)
        got = await self.svc.get(job.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.prompt, "喝水")
        self.assertTrue(await self.svc.remove(job.id))
        self.assertEqual(await self.svc.list_jobs(user_id="admin", platform="admin"), [])

    async def test_enable_and_ownership(self):
        job = await self.svc.add(
            kind="every",
            schedule="1h",
            prompt="查天气",
            platform="admin",
            user_id="admin",
            channel_type="console",
            chat_id="admin",
        )
        off = await self.svc.set_enabled(job.id, False)
        self.assertFalse(off.enabled)
        other = await self.svc.owned(job.id, platform="napcat", user_id="1")
        self.assertIsNone(other)
        mine = await self.svc.owned(job.id, platform="admin", user_id="admin")
        self.assertIsNotNone(mine)

    async def test_at_deletes_after_success(self):
        job = await self.svc.add(
            kind="at",
            schedule="1m",
            prompt="一次性",
            platform="admin",
            user_id="admin",
            channel_type="console",
            chat_id="admin",
        )
        await self.svc.fire(job, force=True)
        self.assertEqual(self.fired, [job.id])
        self.assertIsNone(await self.svc.get(job.id))

    async def test_failure_disables(self):
        async def boom(job):
            raise RuntimeError("nope")

        self.svc.attach_runner(boom)
        job = await self.svc.add(
            kind="every",
            schedule="1h",
            prompt="失败",
            platform="admin",
            user_id="admin",
            channel_type="console",
            chat_id="admin",
        )
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            current = await self.svc.get(job.id)
            await self.svc.fire(current, force=True)
        done = await self.svc.get(job.id)
        self.assertFalse(done.enabled)
        self.assertEqual(done.consecutive_failures, MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(done.last_status, "error")

    async def test_due_jobs(self):
        past = utc_iso(now_shanghai() - timedelta(seconds=5))
        job = await self.svc.add(
            kind="every",
            schedule="1h",
            prompt="到期",
            platform="admin",
            user_id="admin",
            channel_type="console",
            chat_id="admin",
        )
        job.next_run_at = past
        await self.db.put_cron_job(job.to_row())
        due = await self.db.due_cron_jobs(utc_iso(), limit=10)
        self.assertTrue(any(row["id"] == job.id for row in due))

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from core.clock import now_shanghai, parse_created_at
from core.cron.schedule import next_run_at, resolve_schedule
from core.cron.types import (
    MAX_CONCURRENT_RUNS,
    MAX_CONSECUTIVE_FAILURES,
    MISSED_GRACE_SEC,
    CronJob,
    utc_iso,
)
from utils.log import ChatbotLogger

CronRunner = Callable[[CronJob], Awaitable[str]]


class CronService:
    def __init__(self, db: Any, logger: ChatbotLogger | None = None) -> None:
        self.db = db
        self.logger = logger or ChatbotLogger()
        self._runner: CronRunner | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
        self._stopped = True
        self._running: set[str] = set()

    def attach_runner(self, runner: CronRunner) -> None:
        self._runner = runner

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="luopita-cron")

    async def stop(self) -> None:
        self._stopped = True
        self.wake()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.warning(f"cron loop stop: {exc}")

    async def add(
        self,
        *,
        kind: str = "",
        schedule: str = "",
        prompt: str = "",
        name: str = "",
        platform: str = "",
        channel_type: str = "",
        chat_id: str = "",
        user_id: str = "",
        delete_after_run: bool | None = None,
        now: datetime | None = None,
        enabled: bool = True,
    ) -> CronJob:
        spec, body = resolve_schedule(kind=kind, schedule=schedule, prompt=prompt, now=now)
        text = (body or prompt or "").strip()
        if not text:
            raise ValueError("还差任务内容")
        clock = now_shanghai(now)
        job = CronJob(
            id=await self._new_id(),
            name=(name or "").strip() or text[:24],
            enabled=enabled,
            kind=spec.kind,
            schedule=spec.schedule,
            prompt=text,
            platform=platform,
            channel_type=channel_type,
            chat_id=chat_id,
            user_id=user_id,
            delete_after_run=spec.delete_after_run if delete_after_run is None else delete_after_run,
            next_run_at=utc_iso(next_run_at(spec.kind, spec.schedule, now=clock)),
            created_at=utc_iso(clock),
            updated_at=utc_iso(clock),
        )
        await self.db.put_cron_job(job.to_row())
        self.wake()
        return job

    async def list_jobs(
        self,
        *,
        user_id: str = "",
        platform: str = "",
        include_disabled: bool = True,
    ) -> list[CronJob]:
        enabled = None if include_disabled else True
        rows = await self.db.list_cron_jobs(
            user_id=user_id, platform=platform, enabled=enabled
        )
        jobs = [CronJob.from_row(row) for row in rows]
        return [job for job in jobs if job and job.id]

    async def get(self, job_id: str) -> CronJob | None:
        row = await self.db.get_cron_job((job_id or "").strip())
        return CronJob.from_row(row)

    async def owned(self, job_id: str, *, platform: str, user_id: str) -> CronJob | None:
        job = await self.get(job_id)
        if job is None or not job.owned_by(platform=platform, user_id=user_id):
            return None
        return job

    async def remove(self, job_id: str) -> bool:
        ok = await self.db.delete_cron_job((job_id or "").strip())
        if ok:
            self.wake()
        return bool(ok)

    async def set_enabled(self, job_id: str, enabled: bool, *, now: datetime | None = None) -> CronJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.enabled = bool(enabled)
        if job.enabled and not job.next_run_at:
            job.next_run_at = utc_iso(next_run_at(job.kind, job.schedule, now=now))
        job.updated_at = utc_iso()
        await self.db.put_cron_job(job.to_row())
        self.wake()
        return job

    async def update(
        self,
        job_id: str,
        *,
        kind: str = "",
        schedule: str = "",
        prompt: str = "",
        name: str = "",
        enabled: bool | None = None,
        now: datetime | None = None,
    ) -> CronJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        if kind or schedule or prompt:
            spec, body = resolve_schedule(
                kind=kind or job.kind,
                schedule=schedule or job.schedule,
                prompt=prompt or job.prompt,
                now=now,
            )
            job.kind = spec.kind
            job.schedule = spec.schedule
            if body:
                job.prompt = body
            if not schedule and kind:
                pass
            job.delete_after_run = spec.delete_after_run
            job.next_run_at = utc_iso(next_run_at(job.kind, job.schedule, now=now))
        if name.strip():
            job.name = name.strip()
        if enabled is not None:
            job.enabled = bool(enabled)
        job.updated_at = utc_iso()
        await self.db.put_cron_job(job.to_row())
        self.wake()
        return job

    async def run_now(self, job_id: str) -> CronJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        await self.fire(job, force=True)
        return await self.get(job_id)

    async def fire(self, job: CronJob, *, force: bool = False) -> str:
        if job.id in self._running:
            return "already running"
        self._running.add(job.id)
        try:
            async with self._sem:
                return await self._fire_locked(job, force=force)
        finally:
            self._running.discard(job.id)

    async def _fire_locked(self, job: CronJob, *, force: bool) -> str:
        clock = now_shanghai()
        due_at = parse_created_at(job.next_run_at)
        if not force and due_at is not None:
            lag = (clock - due_at).total_seconds()
            if lag > MISSED_GRACE_SEC:
                await self._mark_skipped(job, clock)
                return "skipped"
        if job.kind in {"every", "cron"}:
            job.next_run_at = utc_iso(next_run_at(job.kind, job.schedule, now=clock))
            job.updated_at = utc_iso(clock)
            await self.db.put_cron_job(job.to_row())
            self.wake()
        runner = self._runner
        if runner is None:
            await self._mark_error(job, clock, "runner missing")
            return "error"
        try:
            reply = await runner(job)
        except Exception as exc:
            await self._mark_error(job, clock, str(exc))
            self.logger.warning(f"cron fire failed id={job.id}: {exc}")
            return "error"
        current = await self.get(job.id)
        if current is None:
            return reply
        current.last_run_at = utc_iso(clock)
        current.last_status = "ok"
        current.last_error = ""
        current.consecutive_failures = 0
        current.updated_at = utc_iso(clock)
        if current.delete_after_run:
            await self.db.delete_cron_job(current.id)
            self.wake()
            return reply
        if current.kind == "at":
            current.enabled = False
        await self.db.put_cron_job(current.to_row())
        return reply

    async def _mark_skipped(self, job: CronJob, clock: datetime) -> None:
        if job.kind == "at" or job.delete_after_run:
            await self.db.delete_cron_job(job.id)
            self.wake()
            return
        job.last_status = "skipped"
        job.last_error = "missed"
        job.next_run_at = utc_iso(next_run_at(job.kind, job.schedule, now=clock))
        job.updated_at = utc_iso(clock)
        await self.db.put_cron_job(job.to_row())
        self.wake()

    async def _mark_error(self, job: CronJob, clock: datetime, error: str) -> None:
        current = await self.get(job.id) or job
        current.last_run_at = utc_iso(clock)
        current.last_status = "error"
        current.last_error = (error or "")[:240]
        current.consecutive_failures = int(current.consecutive_failures or 0) + 1
        if current.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            current.enabled = False
        else:
            delay = min(3600, 60 * current.consecutive_failures)
            current.next_run_at = utc_iso(clock + timedelta(seconds=delay))
        current.updated_at = utc_iso(clock)
        await self.db.put_cron_job(current.to_row())
        self.wake()

    async def _new_id(self) -> str:
        for _ in range(8):
            job_id = uuid.uuid4().hex[:8]
            if await self.db.get_cron_job(job_id) is None:
                return job_id
        return uuid.uuid4().hex[:12]

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                wait = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning(f"cron tick: {exc}")
                wait = 15.0
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.2, wait))
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise

    async def _tick(self) -> float:
        clock = now_shanghai()
        rows = await self.db.due_cron_jobs(utc_iso(clock), limit=20)
        for row in rows:
            job = CronJob.from_row(row)
            if job is None or not job.id or job.id in self._running:
                continue
            asyncio.create_task(self._safe_fire(job), name=f"cron-{job.id}")
        nxt = await self._seconds_until_next(clock)
        return nxt

    async def _safe_fire(self, job: CronJob) -> None:
        try:
            await self.fire(job, force=False)
        except Exception as exc:
            self.logger.warning(f"cron task {job.id}: {exc}")

    async def _seconds_until_next(self, clock: datetime) -> float:
        rows = await self.db.list_cron_jobs(enabled=True)
        soonest: datetime | None = None
        for row in rows:
            job = CronJob.from_row(row)
            if job is None or not job.next_run_at or job.id in self._running:
                continue
            due = parse_created_at(job.next_run_at)
            if due is None:
                continue
            if soonest is None or due < soonest:
                soonest = due
        if soonest is None:
            return 60.0
        delta = (soonest - clock).total_seconds()
        if delta <= 0:
            return 0.25
        return min(60.0, max(0.25, delta))

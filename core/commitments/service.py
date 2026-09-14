from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from core.clock import now_shanghai, parse_created_at
from core.commitments.extract import extract_commitment_ops
from core.commitments.types import Commitment, utc_iso
from utils.log import ChatbotLogger

HEARTBEAT_MAX_PER_OWNER_HOUR = 3
NOTIFY_COOLDOWN_SEC = 50 * 60


class CommitmentService:
    def __init__(self, db: Any, logger: ChatbotLogger | None = None) -> None:
        self.db = db
        self.logger = logger or ChatbotLogger()
        self._proactive_hits: dict[str, list[float]] = {}

    async def _new_id(self) -> str:
        for _ in range(8):
            cid = uuid.uuid4().hex[:8]
            if await self.db.get_commitment(cid) is None:
                return cid
        return uuid.uuid4().hex[:12]

    async def add(
        self,
        *,
        text: str,
        due_at: str = "",
        platform: str = "",
        channel_type: str = "",
        chat_id: str = "",
        user_id: str = "",
        source_session: str = "",
        evidence: str = "",
    ) -> Commitment:
        item = Commitment(
            id=await self._new_id(),
            text=(text or "").strip()[:160],
            status="open",
            due_at=due_at or utc_iso(now_shanghai()),
            platform=platform,
            channel_type=channel_type or "private",
            chat_id=chat_id,
            user_id=user_id,
            source_session=source_session,
            evidence=evidence[:200],
        )
        await self.db.put_commitment(item.to_row())
        return item

    async def list_open(
        self,
        *,
        user_id: str = "",
        platform: str = "",
        limit: int = 20,
    ) -> list[Commitment]:
        rows = await self.db.list_commitments(
            user_id=user_id,
            platform=platform,
            status="open",
            limit=limit,
        )
        return [c for c in (Commitment.from_row(row) for row in rows) if c and c.id]

    async def due_open(self, *, now: datetime | None = None, limit: int = 20) -> list[Commitment]:
        clock = now_shanghai(now)
        rows = await self.db.due_commitments(utc_iso(clock), limit=limit)
        return [c for c in (Commitment.from_row(row) for row in rows) if c and c.id]

    async def mark_notified(self, commitment_id: str, *, now: datetime | None = None) -> Commitment | None:
        row = await self.db.get_commitment(commitment_id)
        item = Commitment.from_row(row)
        if item is None:
            return None
        item.last_notified_at = utc_iso(now_shanghai(now))
        item.updated_at = item.last_notified_at
        await self.db.put_commitment(item.to_row())
        return item

    async def close(
        self,
        commitment_id: str,
        *,
        status: str = "done",
    ) -> Commitment | None:
        row = await self.db.get_commitment(commitment_id)
        item = Commitment.from_row(row)
        if item is None:
            return None
        item.status = "cancelled" if status == "cancelled" else "done"
        item.updated_at = utc_iso()
        await self.db.put_commitment(item.to_row())
        return item

    async def cancel_matching(
        self,
        *,
        platform: str,
        user_id: str,
        hint: str = "",
    ) -> list[Commitment]:
        open_items = await self.list_open(platform=platform, user_id=user_id, limit=30)
        if not open_items:
            return []
        hint_l = (hint or "").lower()
        matched = []
        for item in open_items:
            if not hint_l or hint_l[:8] in item.text.lower() or item.text.lower()[:8] in hint_l:
                matched.append(item)
        if not matched and open_items:
            matched = [open_items[0]]
        closed: list[Commitment] = []
        for item in matched[:3]:
            done = await self.close(item.id, status="cancelled")
            if done:
                closed.append(done)
        return closed

    async def ingest_turn(
        self,
        *,
        user_text: str,
        platform: str,
        channel_type: str,
        chat_id: str,
        user_id: str,
        source_session: str = "",
    ) -> list[Commitment]:
        ops = extract_commitment_ops(user_text)
        changed: list[Commitment] = []
        for op in ops:
            if op.action == "cancel":
                closed = await self.cancel_matching(
                    platform=platform,
                    user_id=user_id,
                    hint=op.text,
                )
                changed.extend(closed)
                continue
            item = await self.add(
                text=op.text,
                due_at=op.due_at,
                platform=platform,
                channel_type=channel_type,
                chat_id=chat_id,
                user_id=user_id,
                source_session=source_session,
                evidence=op.evidence,
            )
            changed.append(item)
        return changed

    def allow_proactive(self, *, platform: str, user_id: str, now: datetime | None = None) -> bool:
        key = f"{platform}:{user_id}"
        clock = now_shanghai(now).timestamp()
        hits = [t for t in self._proactive_hits.get(key, []) if clock - t < 3600]
        self._proactive_hits[key] = hits
        return len(hits) < HEARTBEAT_MAX_PER_OWNER_HOUR

    def note_proactive(self, *, platform: str, user_id: str, now: datetime | None = None) -> None:
        key = f"{platform}:{user_id}"
        clock = now_shanghai(now).timestamp()
        hits = [t for t in self._proactive_hits.get(key, []) if clock - t < 3600]
        hits.append(clock)
        self._proactive_hits[key] = hits

    def should_notify(self, item: Commitment, *, now: datetime | None = None) -> bool:
        if item.status != "open":
            return False
        clock = now_shanghai(now)
        due = parse_created_at(item.due_at)
        if due is not None and due > clock:
            return False
        last = parse_created_at(item.last_notified_at)
        if last is not None and (clock - last).total_seconds() < NOTIFY_COOLDOWN_SEC:
            return False
        return True

    def format_followup_prompt(self, item: Commitment) -> str:
        return (
            f"这是你之前记下的开放承诺，现在到期该跟进了：{item.text}\n"
            "用一两句私聊口吻提醒主人；不要说自己是定时任务或系统。"
        )

    def summary_for_scratchpad(self, items: list[Commitment], *, limit: int = 3) -> str:
        lines = []
        for item in items[:limit]:
            if item.text:
                lines.append(f"- {item.text}")
        return "\n".join(lines)

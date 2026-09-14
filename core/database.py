from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from utils.config import AppConfig


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database(Protocol):
    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...
    async def ensure_session(
        self,
        session_id: str,
        user_id: str | None = None,
        platform: str | None = None,
        channel_type: str | None = None,
    ) -> None: ...
    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        provider: str | None = None,
        latency_ms: int | None = None,
    ) -> None: ...
    async def load_messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]: ...
    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]: ...
    async def get_settings(self) -> dict[str, Any] | None: ...
    async def put_settings(self, data: dict[str, Any]) -> None: ...
    async def put_memory(self, user_id: str, content: str) -> None: ...
    async def search_memory(self, user_id: str, query: str, limit: int = 5) -> list[str]: ...
    async def clear_session_messages(self, session_id: str) -> None: ...
    async def clear_user_memory(self, user_id: str) -> None: ...
    async def session_ids_for_user(self, user_id: str, *, platform: str = "") -> list[str]: ...
    async def append_group_event(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str = "",
        sender_name: str = "",
        role: str = "user",
        content: str,
        media: list[dict[str, Any]] | None = None,
        message_id: str = "",
        shard_id: str = "",
        embedding: list[float] | None = None,
    ) -> int | None: ...
    async def load_group_recent(
        self, platform: str, chat_id: str, limit: int = 30
    ) -> list[dict[str, Any]]: ...
    async def put_cron_job(self, job: dict[str, Any]) -> None: ...
    async def get_cron_job(self, job_id: str) -> dict[str, Any] | None: ...
    async def list_cron_jobs(
        self,
        *,
        user_id: str = "",
        platform: str = "",
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]: ...
    async def delete_cron_job(self, job_id: str) -> bool: ...
    async def due_cron_jobs(self, now_iso: str, limit: int = 20) -> list[dict[str, Any]]: ...


class InMemoryDatabase:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.settings: dict[str, Any] | None = None
        self.memories: dict[str, list[dict[str, Any]]] = {}
        self.group_events: dict[str, list[dict[str, Any]]] = {}
        self.group_shards: dict[str, dict[str, Any]] = {}
        self.shard_summaries: dict[str, str] = {}
        self.shard_turns: dict[str, list[dict[str, Any]]] = {}
        self.user_profiles: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._msg_shard: dict[tuple[str, str, str], str] = {}
        self._user_active: dict[tuple[str, str, str], str] = {}
        self.cron_jobs: dict[str, dict[str, Any]] = {}

    async def init(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def ensure_session(
        self,
        session_id: str,
        user_id: str | None = None,
        platform: str | None = None,
        channel_type: str | None = None,
    ) -> None:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "session_id": session_id,
                "user_id": user_id,
                "platform": platform,
                "channel_type": channel_type,
                "created_at": utc_now(),
            }
            self.messages[session_id] = []
        else:
            row = self.sessions[session_id]
            if user_id:
                row["user_id"] = user_id
            if platform:
                row["platform"] = platform
            if channel_type:
                row["channel_type"] = channel_type

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        provider: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        await self.ensure_session(session_id)
        self.messages.setdefault(session_id, []).append(
            {
                "id": len(self.messages[session_id]) + 1,
                "session_id": session_id,
                "role": role,
                "content": content,
                "provider": provider,
                "latency_ms": latency_ms,
                "created_at": utc_now(),
            }
        )

    async def load_messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.messages.get(session_id, [])
        return rows[-limit:]

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self.sessions.values())
        items.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        out = []
        for row in items[:limit]:
            msgs = self.messages.get(row["session_id"], [])
            preview = msgs[-1]["content"] if msgs else ""
            out.append({**row, "message_count": len(msgs), "preview": preview[:120]})
        return out

    async def get_settings(self) -> dict[str, Any] | None:
        return self.settings

    async def put_settings(self, data: dict[str, Any]) -> None:
        self.settings = data

    async def put_memory(self, user_id: str, content: str, embedding: list[float] | None = None) -> None:
        self.memories.setdefault(user_id, []).append(
            {
                "id": str(uuid.uuid4()),
                "content": content,
                "embedding": list(embedding) if embedding else None,
                "created_at": utc_now(),
            }
        )

    async def search_memory(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[str]:
        from core.group_context.keys import cosine_similarity, hash_embed

        rows = list(self.memories.get(user_id, []))
        if query_embedding is None and (query or "").strip():
            query_embedding = hash_embed(query)
        if query_embedding:
            scored = []
            for row in rows:
                emb = row.get("embedding")
                if not emb:
                    emb = hash_embed(str(row.get("content") or ""))
                scored.append((cosine_similarity(query_embedding, emb), row["content"]))
            scored.sort(key=lambda item: item[0], reverse=True)
            if scored and scored[0][0] > 0:
                return [content for _, content in scored[:limit]]
        q = (query or "").strip().lower()
        if q:
            ranked = [r for r in rows if q in r["content"].lower()] or rows
        else:
            ranked = rows
        return [r["content"] for r in ranked[-limit:]][::-1]

    async def clear_session_messages(self, session_id: str) -> None:
        self.messages[session_id] = []

    async def clear_user_memory(self, user_id: str) -> None:
        self.memories.pop(user_id, None)

    async def session_ids_for_user(self, user_id: str, *, platform: str = "") -> list[str]:
        uid = (user_id or "").strip()
        plat = (platform or "").strip().lower()
        out = []
        for row in self.sessions.values():
            if str(row.get("user_id") or "") != uid:
                continue
            if plat and str(row.get("platform") or "").lower() != plat:
                continue
            out.append(str(row.get("session_id") or ""))
        return [sid for sid in out if sid]

    def _group_key(self, platform: str, chat_id: str) -> str:
        return f"{platform}:{chat_id}"

    async def append_group_event(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str = "",
        sender_name: str = "",
        role: str = "user",
        content: str,
        media: list[dict[str, Any]] | None = None,
        message_id: str = "",
        shard_id: str = "",
        embedding: list[float] | None = None,
    ) -> int | None:
        if not platform or not chat_id:
            return None
        payload = list(media or [])
        text = (content or "").strip()
        if not text and not payload:
            return None
        if not text:
            text = "[图片]"
        key = self._group_key(platform, chat_id)
        rows = self.group_events.setdefault(key, [])
        event_id = len(rows) + 1
        mid = (message_id or "").strip()
        sid = (shard_id or "").strip()
        rows.append(
            {
                "id": event_id,
                "platform": platform,
                "chat_id": chat_id,
                "user_id": user_id or "",
                "sender_name": sender_name or "",
                "role": role or "user",
                "content": text,
                "media": payload,
                "message_id": mid,
                "shard_id": sid,
                "embedding": list(embedding) if embedding else None,
                "created_at": utc_now(),
            }
        )
        if mid and sid:
            self._msg_shard[(platform, chat_id, mid)] = sid
        return event_id

    async def load_group_recent(self, platform: str, chat_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.group_events.get(self._group_key(platform, chat_id), [])
        return list(rows[-max(1, limit) :])

    async def upsert_group_shard(
        self,
        *,
        shard_id: str,
        platform: str,
        chat_id: str,
        user_id: str,
        topic: str = "",
        centroid: list[float] | None = None,
    ) -> None:
        row = self.group_shards.get(shard_id) or {
            "shard_id": shard_id,
            "platform": platform,
            "chat_id": chat_id,
            "user_id": user_id,
            "topic": topic,
            "centroid": [],
            "created_at": utc_now(),
        }
        row.update(
            {
                "platform": platform,
                "chat_id": chat_id,
                "user_id": user_id,
                "topic": topic or row.get("topic") or "",
                "centroid": list(centroid) if centroid is not None else row.get("centroid") or [],
                "updated_at": utc_now(),
            }
        )
        self.group_shards[shard_id] = row
        self._user_active[(platform, chat_id, user_id)] = shard_id

    async def get_group_shard(self, shard_id: str) -> dict[str, Any] | None:
        row = self.group_shards.get(shard_id)
        return dict(row) if row else None

    async def get_user_active_shard(self, platform: str, chat_id: str, user_id: str) -> str:
        return self._user_active.get((platform, chat_id, user_id), "")

    async def clear_user_group_shards(self, platform: str, chat_id: str, user_id: str) -> list[str]:
        self._user_active.pop((platform, chat_id, user_id), None)
        removed: list[str] = []
        for shard_id, row in list(self.group_shards.items()):
            if (
                str(row.get("platform") or "") == platform
                and str(row.get("chat_id") or "") == chat_id
                and str(row.get("user_id") or "") == user_id
            ):
                removed.append(shard_id)
                self.group_shards.pop(shard_id, None)
                self.shard_summaries.pop(shard_id, None)
                self.shard_turns.pop(shard_id, None)
        return removed

    async def shard_for_message(self, platform: str, chat_id: str, message_id: str) -> str:
        mid = (message_id or "").strip()
        if not mid:
            return ""
        cached = self._msg_shard.get((platform, chat_id, mid), "")
        if cached:
            return cached
        for event in self.group_events.get(self._group_key(platform, chat_id), []):
            if str(event.get("message_id") or "") == mid and event.get("shard_id"):
                return str(event["shard_id"])
        return ""

    async def is_bot_group_message(self, platform: str, chat_id: str, message_id: str, bot_id: str) -> bool:
        mid = (message_id or "").strip()
        if not mid:
            return False
        for event in self.group_events.get(self._group_key(platform, chat_id), []):
            if str(event.get("message_id") or "") != mid:
                continue
            if str(event.get("role") or "") == "assistant":
                return True
            if bot_id and str(event.get("user_id") or "") == str(bot_id):
                return True
        return False

    async def put_shard_summary(self, shard_id: str, summary: str) -> None:
        self.shard_summaries[shard_id] = (summary or "").strip()

    async def get_shard_summary(self, shard_id: str) -> str:
        return self.shard_summaries.get(shard_id, "")

    async def append_shard_turn(self, *, shard_id: str, turn: dict[str, Any]) -> None:
        rows = self.shard_turns.setdefault(shard_id, [])
        rows.append({**turn, "created_at": utc_now()})

    async def load_shard_turns(self, shard_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.shard_turns.get(shard_id, [])
        return list(rows[-max(1, limit) :])

    async def get_user_profile(
        self, platform: str, user_id: str, chat_id: str = ""
    ) -> dict[str, Any] | None:
        row = self.user_profiles.get((platform, chat_id, user_id))
        return dict(row) if row else None

    async def put_user_profile(
        self,
        *,
        platform: str,
        user_id: str,
        chat_id: str = "",
        display_name: str = "",
        preferences: str = "",
        notes: str = "",
        card: dict[str, Any] | None = None,
    ) -> None:
        key = (platform, chat_id, user_id)
        existing = self.user_profiles.get(key) or {}
        stored_card = card if card is not None else existing.get("card") or {}
        if not isinstance(stored_card, dict):
            stored_card = {}
        self.user_profiles[key] = {
            "platform": platform,
            "chat_id": chat_id,
            "user_id": user_id,
            "display_name": display_name or str(existing.get("display_name") or ""),
            "preferences": preferences if preferences else str(existing.get("preferences") or ""),
            "notes": notes if notes else str(existing.get("notes") or ""),
            "card": dict(stored_card),
            "updated_at": utc_now(),
        }

    async def clear_user_profile(self, platform: str, chat_id: str, user_id: str) -> None:
        self.user_profiles.pop((platform, chat_id, user_id), None)

    async def list_user_profiles(self, limit: int = 100) -> list[dict[str, Any]]:
        items = [dict(row) for row in self.user_profiles.values()]
        items.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return items[: max(1, int(limit or 100))]

    async def search_group_memory(
        self,
        platform: str,
        chat_id: str,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        from core.group_context.keys import cosine_similarity, hash_embed

        rows = self.group_events.get(self._group_key(platform, chat_id), [])
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            emb = row.get("embedding")
            if not emb:
                emb = hash_embed(str(row.get("content") or ""))
            scored.append((cosine_similarity(query_embedding, emb), row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [dict(row) for score, row in scored[:limit] if score > 0.05]

    async def put_cron_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("id") or "").strip()
        if not job_id:
            raise ValueError("cron job id required")
        self.cron_jobs[job_id] = dict(job)

    async def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.cron_jobs.get((job_id or "").strip())
        return dict(row) if row else None

    async def list_cron_jobs(
        self,
        *,
        user_id: str = "",
        platform: str = "",
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        items = []
        for row in self.cron_jobs.values():
            if user_id and str(row.get("user_id") or "") != user_id:
                continue
            if platform and str(row.get("platform") or "") != platform:
                continue
            if enabled is not None and bool(row.get("enabled")) != bool(enabled):
                continue
            items.append(dict(row))
        items.sort(key=lambda r: str(r.get("next_run_at") or ""))
        return items

    async def delete_cron_job(self, job_id: str) -> bool:
        return self.cron_jobs.pop((job_id or "").strip(), None) is not None

    async def due_cron_jobs(self, now_iso: str, limit: int = 20) -> list[dict[str, Any]]:
        from core.clock import parse_created_at

        now = parse_created_at(now_iso)
        items = []
        for row in self.cron_jobs.values():
            if not row.get("enabled"):
                continue
            due = parse_created_at(row.get("next_run_at"))
            if due is None:
                continue
            if now is not None and due > now:
                continue
            items.append(dict(row))
        items.sort(key=lambda r: str(r.get("next_run_at") or ""))
        return items[: max(1, limit)]


_INIT_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT,
    platform TEXT,
    channel_type TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    provider TEXT,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
CREATE TABLE IF NOT EXISTS bot_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS long_term_memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memories_user_id ON long_term_memories(user_id);
CREATE TABLE IF NOT EXISTS group_events (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    media JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_group_events_room ON group_events(platform, chat_id, id);
CREATE TABLE IF NOT EXISTS group_shards (
    shard_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    centroid JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_group_shards_user
    ON group_shards(platform, chat_id, user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS shard_summaries (
    shard_id TEXT PRIMARY KEY REFERENCES group_shards(shard_id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS user_profiles (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    preferences TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    card JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (platform, chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS shard_turns (
    id BIGSERIAL PRIMARY KEY,
    shard_id TEXT NOT NULL REFERENCES group_shards(shard_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_shard_turns_shard ON shard_turns(shard_id, id);
CREATE TABLE IF NOT EXISTS cron_jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    kind TEXT NOT NULL,
    schedule TEXT NOT NULL,
    tz TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    prompt TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    channel_type TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    delete_after_run BOOLEAN NOT NULL DEFAULT FALSE,
    next_run_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_status TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cron_jobs_next ON cron_jobs(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_cron_jobs_owner ON cron_jobs(platform, user_id);
"""


class PostgresDatabase:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool = None

    async def init(self) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        self.pool = AsyncConnectionPool(
            conninfo=self.database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await self.pool.open()
        async with self.pool.connection() as conn:
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception:
                pass
            await conn.execute(_INIT_SQL)
            await conn.execute(
                "ALTER TABLE group_events ADD COLUMN IF NOT EXISTS media JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
            await conn.execute("ALTER TABLE group_events ADD COLUMN IF NOT EXISTS message_id TEXT NOT NULL DEFAULT ''")
            await conn.execute("ALTER TABLE group_events ADD COLUMN IF NOT EXISTS shard_id TEXT NOT NULL DEFAULT ''")
            try:
                await conn.execute(
                    "ALTER TABLE long_term_memories ADD COLUMN IF NOT EXISTS embedding vector(1536)"
                )
            except Exception:
                pass
            try:
                await conn.execute("ALTER TABLE group_events ADD COLUMN IF NOT EXISTS embedding vector(1536)")
            except Exception:
                pass
            await conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS chat_id TEXT NOT NULL DEFAULT ''"
            )
            await conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS card JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
            try:
                await conn.execute("ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS user_profiles_pkey")
                await conn.execute(
                    "ALTER TABLE user_profiles ADD PRIMARY KEY (platform, chat_id, user_id)"
                )
            except Exception:
                pass
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_group_events_message ON group_events(platform, chat_id, message_id)"
            )
            await conn.commit()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def ping(self) -> bool:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute("SELECT 1")
        return True

    async def ensure_session(
        self,
        session_id: str,
        user_id: str | None = None,
        platform: str | None = None,
        channel_type: str | None = None,
    ) -> None:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO sessions(session_id, user_id, platform, channel_type)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    user_id = COALESCE(EXCLUDED.user_id, sessions.user_id),
                    platform = COALESCE(EXCLUDED.platform, sessions.platform),
                    channel_type = COALESCE(EXCLUDED.channel_type, sessions.channel_type)
                """,
                (session_id, user_id, platform, channel_type),
            )
            await conn.commit()

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        provider: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        await self.ensure_session(session_id)
        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO messages(session_id, role, content, provider, latency_ms)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (session_id, role, content, provider, latency_ms),
            )
            await conn.commit()

    async def load_messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, session_id, role, content, provider, latency_ms, created_at
                FROM messages
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = await cur.fetchall()
        rows.reverse()
        return [dict(r) for r in rows]

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT s.session_id, s.user_id, s.platform, s.channel_type, s.created_at,
                       COUNT(m.id) AS message_count,
                       COALESCE(
                         (SELECT m2.content FROM messages m2
                          WHERE m2.session_id = s.session_id
                          ORDER BY m2.id DESC LIMIT 1),
                         ''
                       ) AS preview
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.session_id
                GROUP BY s.session_id
                ORDER BY s.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            item = dict(r)
            preview = item.get("preview") or ""
            item["preview"] = preview[:120]
            item["created_at"] = str(item.get("created_at") or "")
            out.append(item)
        return out

    async def get_settings(self) -> dict[str, Any] | None:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT data FROM bot_settings WHERE id = 1")
            row = await cur.fetchone()
        if not row:
            return None
        return dict(row["data"]) if isinstance(row["data"], dict) else None

    async def put_settings(self, data: dict[str, Any]) -> None:
        import json

        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO bot_settings(id, data, updated_at)
                VALUES (1, %s::jsonb, now())
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                """,
                (json.dumps(data),),
            )
            await conn.commit()

    async def put_memory(self, user_id: str, content: str, embedding: list[float] | None = None) -> None:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            if embedding:
                await conn.execute(
                    """
                    INSERT INTO long_term_memories(id, user_id, content, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    (str(uuid.uuid4()), user_id, content, _vector_literal(embedding)),
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO long_term_memories(id, user_id, content)
                    VALUES (%s, %s, %s)
                    """,
                    (str(uuid.uuid4()), user_id, content),
                )
            await conn.commit()

    async def search_memory(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[str]:
        assert self.pool is not None
        q = (query or "").strip()
        async with self.pool.connection() as conn:
            if query_embedding:
                try:
                    cur = await conn.execute(
                        """
                        SELECT content FROM long_term_memories
                        WHERE user_id = %s AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (user_id, _vector_literal(query_embedding), limit),
                    )
                    rows = await cur.fetchall()
                    if rows:
                        return [r["content"] for r in rows]
                except Exception:
                    pass
            if q:
                cur = await conn.execute(
                    """
                    SELECT content FROM long_term_memories
                    WHERE user_id = %s AND content ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, f"%{q}%", limit),
                )
                rows = await cur.fetchall()
                if not rows:
                    cur = await conn.execute(
                        """
                        SELECT content FROM long_term_memories
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (user_id, limit),
                    )
                    rows = await cur.fetchall()
            else:
                cur = await conn.execute(
                    """
                    SELECT content FROM long_term_memories
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = await cur.fetchall()
        return [r["content"] for r in rows]

    async def clear_session_messages(self, session_id: str) -> None:
        if not self.pool or not session_id:
            return
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))

    async def clear_user_memory(self, user_id: str) -> None:
        if not self.pool or not user_id:
            return
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM long_term_memories WHERE user_id = %s", (user_id,))

    async def session_ids_for_user(self, user_id: str, *, platform: str = "") -> list[str]:
        if not self.pool or not user_id:
            return []
        plat = (platform or "").strip().lower()
        async with self.pool.connection() as conn:
            if plat:
                cur = await conn.execute(
                    "SELECT session_id FROM sessions WHERE user_id = %s AND platform = %s",
                    (user_id, plat),
                )
            else:
                cur = await conn.execute(
                    "SELECT session_id FROM sessions WHERE user_id = %s",
                    (user_id,),
                )
            rows = await cur.fetchall()
        return [str(r["session_id"]) for r in rows if r.get("session_id")]

    async def append_group_event(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str = "",
        sender_name: str = "",
        role: str = "user",
        content: str,
        media: list[dict[str, Any]] | None = None,
        message_id: str = "",
        shard_id: str = "",
        embedding: list[float] | None = None,
    ) -> int | None:
        if not platform or not chat_id:
            return None
        payload = list(media or [])
        text = (content or "").strip()
        if not text and not payload:
            return None
        if not text:
            text = "[图片]"
        assert self.pool is not None
        async with self.pool.connection() as conn:
            if embedding:
                cur = await conn.execute(
                    """
                    INSERT INTO group_events(
                        platform, chat_id, user_id, sender_name, role, content, media, message_id, shard_id, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::vector)
                    RETURNING id
                    """,
                    (
                        platform,
                        chat_id,
                        user_id or "",
                        sender_name or "",
                        role or "user",
                        text,
                        json.dumps(payload, ensure_ascii=False),
                        message_id or "",
                        shard_id or "",
                        _vector_literal(embedding),
                    ),
                )
            else:
                cur = await conn.execute(
                    """
                    INSERT INTO group_events(
                        platform, chat_id, user_id, sender_name, role, content, media, message_id, shard_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    RETURNING id
                    """,
                    (
                        platform,
                        chat_id,
                        user_id or "",
                        sender_name or "",
                        role or "user",
                        text,
                        json.dumps(payload, ensure_ascii=False),
                        message_id or "",
                        shard_id or "",
                    ),
                )
            row = await cur.fetchone()
            await conn.commit()
        return int(row["id"]) if row and row.get("id") is not None else None

    async def load_group_recent(self, platform: str, chat_id: str, limit: int = 30) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, platform, chat_id, user_id, sender_name, role, content, media,
                       message_id, shard_id, created_at
                FROM group_events
                WHERE platform = %s AND chat_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (platform, chat_id, max(1, limit)),
            )
            rows = await cur.fetchall()
        rows.reverse()
        out = []
        for row in rows:
            item = dict(row)
            item["created_at"] = str(item.get("created_at") or "")
            raw_media = item.get("media")
            if isinstance(raw_media, str):
                try:
                    raw_media = json.loads(raw_media)
                except json.JSONDecodeError:
                    raw_media = []
            item["media"] = list(raw_media or [])
            out.append(item)
        return out

    async def upsert_group_shard(
        self,
        *,
        shard_id: str,
        platform: str,
        chat_id: str,
        user_id: str,
        topic: str = "",
        centroid: list[float] | None = None,
    ) -> None:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO group_shards(shard_id, platform, chat_id, user_id, topic, centroid, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (shard_id) DO UPDATE SET
                    topic = COALESCE(NULLIF(EXCLUDED.topic, ''), group_shards.topic),
                    centroid = COALESCE(EXCLUDED.centroid, group_shards.centroid),
                    user_id = EXCLUDED.user_id,
                    updated_at = now()
                """,
                (
                    shard_id,
                    platform,
                    chat_id,
                    user_id,
                    topic or "",
                    json.dumps(centroid) if centroid is not None else None,
                ),
            )
            await conn.commit()

    async def get_group_shard(self, shard_id: str) -> dict[str, Any] | None:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT shard_id, platform, chat_id, user_id, topic, centroid, created_at, updated_at
                FROM group_shards WHERE shard_id = %s
                """,
                (shard_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        item = dict(row)
        centroid = item.get("centroid")
        if isinstance(centroid, str):
            try:
                centroid = json.loads(centroid)
            except json.JSONDecodeError:
                centroid = []
        item["centroid"] = list(centroid or [])
        return item

    async def get_user_active_shard(self, platform: str, chat_id: str, user_id: str) -> str:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT shard_id FROM group_shards
                WHERE platform = %s AND chat_id = %s AND user_id = %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (platform, chat_id, user_id),
            )
            row = await cur.fetchone()
        return str(row["shard_id"]) if row else ""

    async def clear_user_group_shards(self, platform: str, chat_id: str, user_id: str) -> list[str]:
        if not self.pool or not platform or not chat_id or not user_id:
            return []
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """
                    DELETE FROM group_shards
                    WHERE platform = %s AND chat_id = %s AND user_id = %s
                    RETURNING shard_id
                    """,
                    (platform, chat_id, user_id),
                )
                rows = await cur.fetchall()
        return [str(r["shard_id"]) for r in rows if r.get("shard_id")]

    async def shard_for_message(self, platform: str, chat_id: str, message_id: str) -> str:
        mid = (message_id or "").strip()
        if not mid:
            return ""
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT shard_id FROM group_events
                WHERE platform = %s AND chat_id = %s AND message_id = %s AND shard_id <> ''
                ORDER BY id DESC LIMIT 1
                """,
                (platform, chat_id, mid),
            )
            row = await cur.fetchone()
        return str(row["shard_id"]) if row and row.get("shard_id") else ""

    async def is_bot_group_message(self, platform: str, chat_id: str, message_id: str, bot_id: str) -> bool:
        mid = (message_id or "").strip()
        if not mid:
            return False
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT role, user_id FROM group_events
                WHERE platform = %s AND chat_id = %s AND message_id = %s
                ORDER BY id DESC LIMIT 1
                """,
                (platform, chat_id, mid),
            )
            row = await cur.fetchone()
        if not row:
            return False
        if str(row.get("role") or "") == "assistant":
            return True
        return bool(bot_id) and str(row.get("user_id") or "") == str(bot_id)

    async def put_shard_summary(self, shard_id: str, summary: str) -> None:
        text = (summary or "").strip()
        if not text:
            return
        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO shard_summaries(shard_id, summary, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (shard_id) DO UPDATE SET summary = EXCLUDED.summary, updated_at = now()
                """,
                (shard_id, text),
            )
            await conn.commit()

    async def get_shard_summary(self, shard_id: str) -> str:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT summary FROM shard_summaries WHERE shard_id = %s",
                (shard_id,),
            )
            row = await cur.fetchone()
        return str(row["summary"]) if row else ""

    async def append_shard_turn(self, *, shard_id: str, turn: dict[str, Any]) -> None:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO shard_turns(shard_id, role, user_id, sender_name, content, message_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    shard_id,
                    str(turn.get("role") or "user"),
                    str(turn.get("user_id") or ""),
                    str(turn.get("sender_name") or ""),
                    str(turn.get("content") or ""),
                    str(turn.get("message_id") or ""),
                ),
            )
            await conn.commit()

    async def load_shard_turns(self, shard_id: str, limit: int = 20) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT role, user_id, sender_name, content, message_id, created_at
                FROM shard_turns WHERE shard_id = %s
                ORDER BY id DESC LIMIT %s
                """,
                (shard_id, max(1, limit)),
            )
            rows = await cur.fetchall()
        rows.reverse()
        out = []
        for row in rows:
            item = dict(row)
            item["created_at"] = str(item.get("created_at") or "")
            out.append(item)
        return out

    async def get_user_profile(
        self, platform: str, user_id: str, chat_id: str = ""
    ) -> dict[str, Any] | None:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT platform, chat_id, user_id, display_name, preferences, notes, card, updated_at
                FROM user_profiles WHERE platform = %s AND chat_id = %s AND user_id = %s
                """,
                (platform, chat_id, user_id),
            )
            row = await cur.fetchone()
        if not row:
            return None
        item = dict(row)
        item["updated_at"] = str(item.get("updated_at") or "")
        card = item.get("card")
        if not isinstance(card, dict):
            item["card"] = {}
        return item

    async def put_user_profile(
        self,
        *,
        platform: str,
        user_id: str,
        chat_id: str = "",
        display_name: str = "",
        preferences: str = "",
        notes: str = "",
        card: dict[str, Any] | None = None,
    ) -> None:
        assert self.pool is not None
        payload = json.dumps(card if isinstance(card, dict) else {}, ensure_ascii=False)
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO user_profiles(platform, chat_id, user_id, display_name, preferences, notes, card, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (platform, chat_id, user_id) DO UPDATE SET
                    display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), user_profiles.display_name),
                    preferences = COALESCE(NULLIF(EXCLUDED.preferences, ''), user_profiles.preferences),
                    notes = COALESCE(NULLIF(EXCLUDED.notes, ''), user_profiles.notes),
                    card = CASE
                        WHEN %s THEN user_profiles.card
                        ELSE EXCLUDED.card
                    END,
                    updated_at = now()
                """,
                (
                    platform,
                    chat_id,
                    user_id,
                    display_name,
                    preferences,
                    notes,
                    payload,
                    card is None,
                ),
            )
            await conn.commit()

    async def clear_user_profile(self, platform: str, chat_id: str, user_id: str) -> None:
        if not self.pool or not platform or not user_id:
            return
        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM user_profiles WHERE platform = %s AND chat_id = %s AND user_id = %s",
                (platform, chat_id, user_id),
            )
            await conn.commit()

    async def list_user_profiles(self, limit: int = 100) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT platform, chat_id, user_id, display_name, preferences, notes, card, updated_at
                FROM user_profiles
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (max(1, int(limit or 100)),),
            )
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["updated_at"] = str(item.get("updated_at") or "")
            if not isinstance(item.get("card"), dict):
                item["card"] = {}
            out.append(item)
        return out

    async def search_group_memory(
        self,
        platform: str,
        chat_id: str,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            try:
                cur = await conn.execute(
                    """
                    SELECT id, platform, chat_id, user_id, sender_name, role, content, media,
                           message_id, shard_id, created_at
                    FROM group_events
                    WHERE platform = %s AND chat_id = %s AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (platform, chat_id, _vector_literal(query_embedding), max(1, limit)),
                )
                rows = await cur.fetchall()
            except Exception:
                cur = await conn.execute(
                    """
                    SELECT id, platform, chat_id, user_id, sender_name, role, content, media,
                           message_id, shard_id, created_at
                    FROM group_events
                    WHERE platform = %s AND chat_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (platform, chat_id, max(1, limit)),
                )
                rows = await cur.fetchall()
                rows.reverse()
        out = []
        for row in rows:
            item = dict(row)
            item["created_at"] = str(item.get("created_at") or "")
            raw_media = item.get("media")
            if isinstance(raw_media, str):
                try:
                    raw_media = json.loads(raw_media)
                except json.JSONDecodeError:
                    raw_media = []
            item["media"] = list(raw_media or [])
            out.append(item)
        return out

    async def put_cron_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("id") or "").strip()
        if not job_id:
            raise ValueError("cron job id required")
        assert self.pool is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO cron_jobs(
                    id, name, enabled, kind, schedule, tz, prompt,
                    platform, channel_type, chat_id, user_id, delete_after_run,
                    next_run_at, last_run_at, last_status, last_error,
                    consecutive_failures, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    enabled = EXCLUDED.enabled,
                    kind = EXCLUDED.kind,
                    schedule = EXCLUDED.schedule,
                    tz = EXCLUDED.tz,
                    prompt = EXCLUDED.prompt,
                    platform = EXCLUDED.platform,
                    channel_type = EXCLUDED.channel_type,
                    chat_id = EXCLUDED.chat_id,
                    user_id = EXCLUDED.user_id,
                    delete_after_run = EXCLUDED.delete_after_run,
                    next_run_at = EXCLUDED.next_run_at,
                    last_run_at = EXCLUDED.last_run_at,
                    last_status = EXCLUDED.last_status,
                    last_error = EXCLUDED.last_error,
                    consecutive_failures = EXCLUDED.consecutive_failures,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    job_id,
                    str(job.get("name") or ""),
                    bool(job.get("enabled", True)),
                    str(job.get("kind") or "at"),
                    str(job.get("schedule") or ""),
                    str(job.get("tz") or "Asia/Shanghai"),
                    str(job.get("prompt") or ""),
                    str(job.get("platform") or ""),
                    str(job.get("channel_type") or ""),
                    str(job.get("chat_id") or ""),
                    str(job.get("user_id") or ""),
                    bool(job.get("delete_after_run", False)),
                    _cron_ts(job.get("next_run_at")),
                    _cron_ts(job.get("last_run_at")),
                    str(job.get("last_status") or ""),
                    str(job.get("last_error") or ""),
                    int(job.get("consecutive_failures") or 0),
                    _cron_ts(job.get("created_at")),
                    _cron_ts(job.get("updated_at")),
                ),
            )
            await conn.commit()

    async def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        sid = (job_id or "").strip()
        if not sid:
            return None
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM cron_jobs WHERE id = %s", (sid,))
            row = await cur.fetchone()
        return _cron_row(row) if row else None

    async def list_cron_jobs(
        self,
        *,
        user_id: str = "",
        platform: str = "",
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        assert self.pool is not None
        clauses = ["TRUE"]
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = %s")
            params.append(user_id)
        if platform:
            clauses.append("platform = %s")
            params.append(platform)
        if enabled is not None:
            clauses.append("enabled = %s")
            params.append(bool(enabled))
        sql = f"""
            SELECT * FROM cron_jobs
            WHERE {' AND '.join(clauses)}
            ORDER BY next_run_at NULLS LAST, id
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
        return [_cron_row(row) for row in rows if row]

    async def delete_cron_job(self, job_id: str) -> bool:
        sid = (job_id or "").strip()
        if not sid:
            return False
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM cron_jobs WHERE id = %s RETURNING id",
                (sid,),
            )
            row = await cur.fetchone()
            await conn.commit()
        return bool(row)

    async def due_cron_jobs(self, now_iso: str, limit: int = 20) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM cron_jobs
                WHERE enabled = TRUE AND next_run_at IS NOT NULL AND next_run_at <= %s
                ORDER BY next_run_at ASC
                LIMIT %s
                """,
                (_cron_ts(now_iso), max(1, limit)),
            )
            rows = await cur.fetchall()
        return [_cron_row(row) for row in rows if row]


def _cron_ts(value: Any):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    from core.clock import parse_created_at

    return parse_created_at(value)


def _cron_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("next_run_at", "last_run_at", "created_at", "updated_at"):
        val = item.get(key)
        if isinstance(val, datetime):
            item[key] = val.isoformat()
        elif val is None:
            item[key] = ""
        else:
            item[key] = str(val)
    item["enabled"] = bool(item.get("enabled"))
    item["delete_after_run"] = bool(item.get("delete_after_run"))
    item["consecutive_failures"] = int(item.get("consecutive_failures") or 0)
    return item


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


def create_database(database_url: str) -> InMemoryDatabase | PostgresDatabase:
    cfg = AppConfig(database_url=database_url)
    if cfg.is_memory_db():
        return InMemoryDatabase()
    if not database_url.startswith("postgres"):
        raise ValueError(f"Unsupported DATABASE_URL: {database_url}")
    return PostgresDatabase(database_url)

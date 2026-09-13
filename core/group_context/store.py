from __future__ import annotations

from typing import Any

from core.group_context.keys import (
    CONV_MAX_TURNS,
    CONV_TTL_SEC,
    ENTITY_STACK_MAX,
    ENTITY_TTL_SEC,
    GROUP_MEM_MAX,
    GROUP_MEM_TTL_SEC,
    SUMMARY_TTL_SEC,
    conv_key,
    entity_key,
    group_mem_key,
    lock_key,
    msg_shard_key,
    summary_key,
    user_active_key,
)
from core.group_context.redis_client import RedisClient, dumps_json, loads_json


class HotStore:
    """Redis (or in-memory) hot layer for active shards and group memory."""

    def __init__(self, redis: RedisClient) -> None:
        self.redis = redis

    async def append_turn(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        shard_id: str,
        turn: dict[str, Any],
    ) -> None:
        key = conv_key(platform, chat_id, user_id, shard_id)
        await self.redis.lpush_trim(key, dumps_json(turn), CONV_MAX_TURNS, CONV_TTL_SEC)
        await self.redis.set(user_active_key(platform, chat_id, user_id), shard_id, CONV_TTL_SEC)
        mid = str(turn.get("message_id") or "").strip()
        if mid:
            await self.bind_message_shard(platform, chat_id, mid, shard_id)

    async def load_turns(
        self,
        *,
        platform: str,
        chat_id: str,
        user_id: str,
        shard_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        key = conv_key(platform, chat_id, user_id, shard_id)
        rows = await self.redis.lrange(key, 0, max(0, limit) - 1)
        out: list[dict[str, Any]] = []
        for raw in reversed(rows):
            item = loads_json(raw, default=None)
            if isinstance(item, dict):
                out.append(item)
        return out

    async def append_group_mem(self, *, platform: str, chat_id: str, event: dict[str, Any]) -> None:
        key = group_mem_key(platform, chat_id)
        await self.redis.lpush_trim(key, dumps_json(event), GROUP_MEM_MAX, GROUP_MEM_TTL_SEC)

    async def load_group_mem(self, *, platform: str, chat_id: str, limit: int = 30) -> list[dict[str, Any]]:
        key = group_mem_key(platform, chat_id)
        rows = await self.redis.lrange(key, 0, max(0, limit) - 1)
        out: list[dict[str, Any]] = []
        for raw in reversed(rows):
            item = loads_json(raw, default=None)
            if isinstance(item, dict):
                out.append(item)
        return out

    async def get_summary(self, shard_id: str) -> str:
        return (await self.redis.get(summary_key(shard_id))) or ""

    async def set_summary(self, shard_id: str, summary: str) -> None:
        text = (summary or "").strip()
        if not text:
            return
        await self.redis.set(summary_key(shard_id), text, SUMMARY_TTL_SEC)

    async def get_active_shard(self, platform: str, chat_id: str, user_id: str) -> str:
        return (await self.redis.get(user_active_key(platform, chat_id, user_id))) or ""

    async def set_active_shard(self, platform: str, chat_id: str, user_id: str, shard_id: str) -> None:
        await self.redis.set(user_active_key(platform, chat_id, user_id), shard_id, CONV_TTL_SEC)

    async def bind_message_shard(self, platform: str, chat_id: str, message_id: str, shard_id: str) -> None:
        mid = (message_id or "").strip()
        if not mid or not shard_id:
            return
        await self.redis.set(msg_shard_key(platform, chat_id, mid), shard_id, CONV_TTL_SEC)

    async def shard_for_message(self, platform: str, chat_id: str, message_id: str) -> str:
        mid = (message_id or "").strip()
        if not mid:
            return ""
        return (await self.redis.get(msg_shard_key(platform, chat_id, mid))) or ""

    async def load_entity_stack(self, platform: str, chat_id: str) -> list[dict[str, Any]]:
        raw = await self.redis.get(entity_key(platform, chat_id))
        data = loads_json(raw, default=[])
        return list(data) if isinstance(data, list) else []

    async def push_entity(
        self,
        platform: str,
        chat_id: str,
        *,
        user_id: str,
        name: str,
        mentioned_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        stack = await self.load_entity_stack(platform, chat_id)
        entry = {
            "user_id": user_id or "",
            "name": (name or "").strip(),
            "mentioned_ids": [str(x) for x in (mentioned_ids or []) if str(x).strip()],
        }
        stack = [entry, *[item for item in stack if item.get("user_id") != entry["user_id"]]]
        stack = stack[:ENTITY_STACK_MAX]
        await self.redis.set(entity_key(platform, chat_id), dumps_json(stack), ENTITY_TTL_SEC)
        return stack

    async def with_shard_lock(self, shard_id: str, ttl_sec: int = 5) -> bool:
        return await self.redis.acquire_lock(lock_key(shard_id), ttl_sec=ttl_sec)

    async def release_shard_lock(self, shard_id: str) -> None:
        await self.redis.release_lock(lock_key(shard_id))

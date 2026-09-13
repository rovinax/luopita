from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol


class RedisClient(Protocol):
    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def lpush_trim(self, key: str, value: str, maxlen: int, ttl_sec: int | None = None) -> None: ...
    async def lrange(self, key: str, start: int = 0, end: int = -1) -> list[str]: ...
    async def acquire_lock(self, key: str, ttl_sec: int = 5) -> bool: ...
    async def release_lock(self, key: str) -> None: ...


class InMemoryRedis:
    """Process-local Redis stand-in for tests and memory:// mode."""

    def __init__(self) -> None:
        self._kv: dict[str, tuple[str, float | None]] = {}
        self._lists: dict[str, tuple[list[str], float | None]] = {}
        self._locks: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _expired(self, expires: float | None) -> bool:
        return expires is not None and time.time() >= expires

    async def init(self) -> None:
        return None

    async def close(self) -> None:
        async with self._lock:
            self._kv.clear()
            self._lists.clear()
            self._locks.clear()

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        async with self._lock:
            row = self._kv.get(key)
            if not row:
                return None
            value, expires = row
            if self._expired(expires):
                self._kv.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
        expires = (time.time() + ttl_sec) if ttl_sec and ttl_sec > 0 else None
        async with self._lock:
            self._kv[key] = (value, expires)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._kv.pop(key, None)
            self._lists.pop(key, None)
            self._locks.pop(key, None)

    async def lpush_trim(self, key: str, value: str, maxlen: int, ttl_sec: int | None = None) -> None:
        expires = (time.time() + ttl_sec) if ttl_sec and ttl_sec > 0 else None
        async with self._lock:
            items, _ = self._lists.get(key, ([], None))
            items = [value, *items]
            if maxlen > 0:
                items = items[:maxlen]
            self._lists[key] = (items, expires)

    async def lrange(self, key: str, start: int = 0, end: int = -1) -> list[str]:
        async with self._lock:
            row = self._lists.get(key)
            if not row:
                return []
            items, expires = row
            if self._expired(expires):
                self._lists.pop(key, None)
                return []
            if end < 0:
                end = len(items) + end
            end = min(end, len(items) - 1)
            if start > end or start >= len(items):
                return []
            return list(items[start : end + 1])

    async def acquire_lock(self, key: str, ttl_sec: int = 5) -> bool:
        now = time.time()
        async with self._lock:
            until = self._locks.get(key)
            if until is not None and until > now:
                return False
            self._locks[key] = now + max(1, ttl_sec)
            return True

    async def release_lock(self, key: str) -> None:
        async with self._lock:
            self._locks.pop(key, None)


class AsyncRedisClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._client: Any = None

    async def init(self) -> None:
        from redis.asyncio import Redis

        self._client = Redis.from_url(self.url, decode_responses=True)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> bool:
        assert self._client is not None
        return bool(await self._client.ping())

    async def get(self, key: str) -> str | None:
        assert self._client is not None
        value = await self._client.get(key)
        return str(value) if value is not None else None

    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
        assert self._client is not None
        if ttl_sec and ttl_sec > 0:
            await self._client.set(key, value, ex=ttl_sec)
        else:
            await self._client.set(key, value)

    async def delete(self, key: str) -> None:
        assert self._client is not None
        await self._client.delete(key)

    async def lpush_trim(self, key: str, value: str, maxlen: int, ttl_sec: int | None = None) -> None:
        assert self._client is not None
        pipe = self._client.pipeline()
        pipe.lpush(key, value)
        if maxlen > 0:
            pipe.ltrim(key, 0, maxlen - 1)
        if ttl_sec and ttl_sec > 0:
            pipe.expire(key, ttl_sec)
        await pipe.execute()

    async def lrange(self, key: str, start: int = 0, end: int = -1) -> list[str]:
        assert self._client is not None
        rows = await self._client.lrange(key, start, end)
        return [str(item) for item in rows]

    async def acquire_lock(self, key: str, ttl_sec: int = 5) -> bool:
        assert self._client is not None
        ok = await self._client.set(key, "1", nx=True, ex=max(1, ttl_sec))
        return bool(ok)

    async def release_lock(self, key: str) -> None:
        assert self._client is not None
        await self._client.delete(key)


def create_redis(redis_url: str) -> InMemoryRedis | AsyncRedisClient:
    url = (redis_url or "").strip()
    if not url or url.lower() in {"memory://", "memory", "none", "off"}:
        return InMemoryRedis()
    return AsyncRedisClient(url)


def dumps_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def loads_json(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default

from __future__ import annotations

from typing import Any

from core.database import Database
from core.group_context.keys import hash_embed
from core.window import compact_dropped

RECALL_MAX_ITEMS = 5
RECALL_MAX_CHARS = 800


class MemoryService:
    """Long-term retrieval. Short-term chat window lives on the LangGraph checkpointer."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def remember_turn(self, user_id: str, user_text: str, reply: str) -> None:
        snippet = f"User: {user_text.strip()}\nAssistant: {reply.strip()[:400]}"
        embedding = hash_embed(snippet)
        if hasattr(self.db, "put_memory"):
            try:
                await self.db.put_memory(user_id=user_id or "anonymous", content=snippet, embedding=embedding)
                return
            except TypeError:
                pass
        await self.db.put_memory(user_id=user_id or "anonymous", content=snippet)

    async def archive_dropped(self, user_id: str, dropped_messages: list[Any] | None) -> str:
        snippet = compact_dropped(dropped_messages or [])
        if not snippet:
            return ""
        embedding = hash_embed(snippet)
        try:
            await self.db.put_memory(user_id=user_id or "anonymous", content=snippet, embedding=embedding)
        except TypeError:
            await self.db.put_memory(user_id=user_id or "anonymous", content=snippet)
        return snippet

    async def forget_user(self, user_id: str) -> None:
        uid = (user_id or "").strip() or "anonymous"
        if hasattr(self.db, "clear_user_memory"):
            await self.db.clear_user_memory(uid)

    async def recall(self, user_id: str, query: str, limit: int = RECALL_MAX_ITEMS) -> list[str]:
        cap = max(1, min(int(limit or RECALL_MAX_ITEMS), RECALL_MAX_ITEMS))
        embedding = hash_embed(query or "") if (query or "").strip() else None
        try:
            return await self.db.search_memory(
                user_id=user_id or "anonymous",
                query=query,
                limit=cap,
                query_embedding=embedding,
            )
        except TypeError:
            return await self.db.search_memory(user_id=user_id or "anonymous", query=query, limit=cap)

    def format_for_prompt(self, memories: list[str]) -> str:
        items: list[str] = []
        total = 0
        for raw in memories[:RECALL_MAX_ITEMS]:
            text = (raw or "").strip()
            if not text:
                continue
            if total + len(text) > RECALL_MAX_CHARS:
                remain = RECALL_MAX_CHARS - total
                if remain < 20:
                    break
                text = text[: remain - 1] + "…"
            items.append(text)
            total += len(text)
        if not items:
            return ""
        bullets = "\n".join(f"- {item}" for item in items)
        return f"Related memory:\n{bullets}"

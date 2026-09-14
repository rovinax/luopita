from __future__ import annotations

import re
from typing import Any

from core.database import Database
from core.group_context.keys import hash_embed
from core.group_talk import is_silence_reply, looks_like_filler
from core.window import compact_dropped

RECALL_MAX_ITEMS = 5
RECALL_MAX_CHARS = 800

_REMEMBER_HINTS = (
    "提醒",
    "记得",
    "别忘",
    "约定",
    "以后",
    "下次",
    "明天",
    "后天",
    "每天",
    "每周",
    "偏好",
    "不要",
    "别用",
    "别列",
    "别说",
    "喜欢",
    "讨厌",
    "项目",
    "叫",
    "我是",
    "我在",
    "正在",
    "deadline",
    "due",
    "todo",
    "remind",
    "prefer",
)

_PREF_RE = re.compile(
    r"(别|不要|别用|别列|别说|不要用|不要列)([^。！？\n]{1,24})",
)
_REMIND_RE = re.compile(
    r"((?:明天|后天|今晚|早上|中午|下午|晚上|每天|每周|下周|过会儿|待会|稍后|"
    r"\d+\s*(?:分钟|小时|天)|remind|todo)[^。！？\n]{0,40}"
    r"(?:提醒|叫我|记得|别忘)[^。！？\n]{0,40})",
    re.I,
)
_REMIND_RE2 = re.compile(
    r"((?:提醒|叫我|记得|别忘)[^。！？\n]{0,48})",
    re.I,
)
_PROJECT_RE = re.compile(
    r"((?:项目|仓库|repo|project)\s*[叫是:]?\s*[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}"
    r"|[A-Za-z][A-Za-z0-9_\-]{2,24}\s*(?:项目|仓库))",
    re.I,
)


def should_remember_turn(user_text: str, reply: str = "") -> bool:
    raw = (user_text or "").strip()
    if not raw:
        return False
    if is_silence_reply(reply):
        return False
    if looks_like_filler(raw):
        return False
    compact = re.sub(r"\s+", "", raw)
    if len(compact) < 6:
        return False
    lowered = raw.lower()
    if any(hint in lowered for hint in _REMEMBER_HINTS):
        return True
    if _PREF_RE.search(raw) or _REMIND_RE.search(raw) or _REMIND_RE2.search(raw):
        return True
    if _PROJECT_RE.search(raw):
        return True
    if len(compact) >= 18 and any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in compact):
        # Substantive turns: keep a short fact line rather than full dialogue dump.
        return True
    return False


def extract_memory_facts(user_text: str, reply: str = "") -> list[str]:
    raw = (user_text or "").strip()
    if not raw:
        return []
    facts: list[str] = []
    seen: set[str] = set()

    def _add(prefix: str, body: str) -> None:
        text = " ".join((body or "").split()).strip(" ，,。.;；")
        if len(text) < 2:
            return
        line = f"{prefix}{text}"[:120]
        key = re.sub(r"\s+", "", line.lower())
        if key in seen:
            return
        # Drop near-duplicates that share the same core phrase.
        for existing in list(seen):
            if key in existing or existing in key:
                return
        seen.add(key)
        facts.append(line)

    for match in _PREF_RE.finditer(raw):
        _add("偏好：", match.group(0))
    remind_hit = False
    for match in _REMIND_RE.finditer(raw):
        _add("约定：", match.group(1) or match.group(0))
        remind_hit = True
    if not remind_hit:
        for match in _REMIND_RE2.finditer(raw):
            _add("约定：", match.group(1) or match.group(0))
    for match in _PROJECT_RE.finditer(raw):
        _add("项目：", match.group(0))
    if not facts and should_remember_turn(raw, reply):
        snippet = " ".join(raw.split())
        if len(snippet) > 80:
            snippet = snippet[:79] + "…"
        _add("聊过：", snippet)
    return facts[:5]


class MemoryService:
    """Long-term retrieval. Short-term chat window lives on the LangGraph checkpointer."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def _put(self, user_id: str, content: str) -> None:
        text = (content or "").strip()
        if not text:
            return
        embedding = hash_embed(text)
        uid = user_id or "anonymous"
        try:
            await self.db.put_memory(user_id=uid, content=text, embedding=embedding)
        except TypeError:
            await self.db.put_memory(user_id=uid, content=text)

    async def remember_fact(self, user_id: str, content: str) -> None:
        await self._put(user_id, content)

    async def remember_turn(self, user_id: str, user_text: str, reply: str) -> None:
        if not should_remember_turn(user_text, reply):
            return
        facts = extract_memory_facts(user_text, reply)
        if facts:
            for fact in facts:
                await self._put(user_id, fact)
            return
        snippet = f"User: {user_text.strip()}\nAssistant: {(reply or '').strip()[:400]}"
        await self._put(user_id, snippet)

    async def archive_dropped(self, user_id: str, dropped_messages: list[Any] | None) -> str:
        snippet = compact_dropped(dropped_messages or [])
        if not snippet:
            return ""
        await self._put(user_id, snippet)
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

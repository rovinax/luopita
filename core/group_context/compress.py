from __future__ import annotations

from typing import Any

from core.group_context.keys import COMPRESS_CHAR_THRESHOLD, SHORT_TURNS
from core.group_context.entity import format_turn_line
from core.group_context.store import HotStore


def turns_char_count(turns: list[dict[str, Any]], bot_name: str = "小Lu") -> int:
    return sum(len(format_turn_line(t, bot_name=bot_name)) for t in turns)


def needs_compression(turns: list[dict[str, Any]], *, threshold: int = COMPRESS_CHAR_THRESHOLD, bot_name: str = "小Lu") -> bool:
    if len(turns) <= SHORT_TURNS:
        return False
    return turns_char_count(turns, bot_name=bot_name) >= threshold


def build_summary_source(turns: list[dict[str, Any]], *, keep_recent: int = SHORT_TURNS, bot_name: str = "小Lu") -> tuple[list[dict[str, Any]], str]:
    """Split older turns for summarization; keep recent verbatim."""
    if len(turns) <= keep_recent:
        return turns, ""
    older = turns[:-keep_recent]
    recent = turns[-keep_recent:]
    blob = "\n".join(format_turn_line(t, bot_name=bot_name) for t in older)
    return recent, blob


def cheap_summary(older_blob: str, existing: str = "") -> str:
    """Fallback summary without LLM: truncate join."""
    text = " ".join((older_blob or "").split())
    if not text:
        return (existing or "").strip()
    snippet = text[:500] + ("…" if len(text) > 500 else "")
    if existing:
        return f"{existing.strip()}；其后：{snippet}"[:800]
    return f"更早对话：{snippet}"


async def maybe_compress_shard(
    *,
    hot: HotStore,
    db: Any,
    shard_id: str,
    turns: list[dict[str, Any]],
    bot_name: str = "小Lu",
    threshold: int = COMPRESS_CHAR_THRESHOLD,
    llm_summarize=None,
) -> str:
    if not needs_compression(turns, threshold=threshold, bot_name=bot_name):
        existing = await hot.get_summary(shard_id)
        if not existing and hasattr(db, "get_shard_summary"):
            existing = await db.get_shard_summary(shard_id)
        return existing or ""

    recent, older_blob = build_summary_source(turns, bot_name=bot_name)
    if not older_blob:
        return ""

    existing = await hot.get_summary(shard_id)
    if not existing and hasattr(db, "get_shard_summary"):
        existing = await db.get_shard_summary(shard_id)

    summary = ""
    if llm_summarize is not None:
        try:
            summary = (await llm_summarize(older_blob, existing or "")).strip()
        except Exception:
            summary = ""
    if not summary:
        summary = cheap_summary(older_blob, existing or "")

    await hot.set_summary(shard_id, summary)
    if hasattr(db, "put_shard_summary"):
        await db.put_shard_summary(shard_id, summary)
    return summary

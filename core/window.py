from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, trim_messages

SHORT_TERM_MAX = 16


def _message_id(message: Any) -> str:
    return str(getattr(message, "id", None) or "")


def _is_human(message: Any) -> bool:
    return getattr(message, "type", "") == "human"


def _is_tool(message: Any) -> bool:
    return getattr(message, "type", "") == "tool"


def _has_tool_calls(message: Any) -> bool:
    return bool(getattr(message, "tool_calls", None))


MAX_TOOL_ROUNDS = 6


def tool_rounds_since_last_human(messages: list[Any] | None) -> int:
    rounds = 0
    for message in reversed(messages or []):
        if _is_human(message):
            break
        if _has_tool_calls(message):
            rounds += 1
    return rounds


def _plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts).strip()


def compact_dropped(dropped: list[Any], limit: int = 400) -> str:
    texts: list[str] = []
    seen: set[str] = set()
    for message in dropped or []:
        if not _is_human(message):
            continue
        text = " ".join(_plain_text(getattr(message, "content", "")).split())
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text[:80])
    if not texts:
        return ""
    snippet = "更早聊过：" + "；".join(texts)
    if len(snippet) > limit:
        return snippet[: limit - 1] + "…"
    return snippet


def _align_start(items: list[BaseMessage], start: int) -> int:
    """Keep the first kept message Human, and do not split tool call pairs."""
    n = len(items)
    if n == 0:
        return 0
    idx = max(0, min(start, n - 1))
    while idx > 0 and _is_tool(items[idx]):
        idx -= 1
    if _is_human(items[idx]):
        return idx
    if _is_tool(items[idx]) or _has_tool_calls(items[idx]):
        while idx > 0 and not _is_human(items[idx]):
            idx -= 1
        return idx
    nxt = idx
    while nxt < n and not _is_human(items[nxt]):
        if _is_tool(items[nxt]) or _has_tool_calls(items[nxt]):
            while idx > 0 and not _is_human(items[idx]):
                idx -= 1
            return idx
        nxt += 1
    if nxt < n and _is_human(items[nxt]):
        return nxt
    while idx > 0 and not _is_human(items[idx]):
        idx -= 1
    return idx


def _original_start(items: list[BaseMessage], first: BaseMessage, keep_len: int) -> int:
    first_id = _message_id(first)
    for index, message in enumerate(items):
        if first_id and _message_id(message) == first_id:
            return index
        if message is first:
            return index
    return max(0, len(items) - keep_len)


def drop_trailing_extra_humans(
    messages: list[BaseMessage] | None,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Keep only the last Human in a trailing Human-only run.

    Unreplied group chatter belongs in group_context, not as this turn's query.
    """
    items = list(messages or [])
    if len(items) < 2 or not _is_human(items[-1]):
        return items, []
    start = len(items) - 1
    while start > 0 and _is_human(items[start - 1]):
        start -= 1
    if start == len(items) - 1:
        return items, []
    parked = items[start:-1]
    kept = items[:start] + items[-1:]
    return kept, parked


def trim_short_term(
    messages: list[BaseMessage] | None,
    max_messages: int = SHORT_TERM_MAX,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    items = list(messages or [])
    cap = max(2, int(max_messages or SHORT_TERM_MAX))
    if len(items) <= cap:
        return items, []
    keep = trim_messages(
        items,
        max_tokens=cap,
        token_counter=len,
        strategy="last",
        start_on="human",
        include_system=False,
    )
    if not keep:
        start = _align_start(items, max(0, len(items) - cap))
    else:
        start = _align_start(items, _original_start(items, keep[0], len(keep)))
    dropped = items[:start]
    kept = items[start:]
    return kept, dropped

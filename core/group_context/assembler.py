from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.group_context.entity import build_entity_hint, format_turn_line, resolve_pronouns
from core.group_context.keys import SHORT_TURNS
from core.group_context.store import HotStore
from core.group_talk import format_group_context

SPEAKER_ISOLATION = (
    "[说话人隔离]\n"
    "只回当前说话人现在这句。"
    "群记忆里其他人与你的未完对话是旁听背景，禁止主动续答、禁止当成当前任务。"
    "仅当当前说话人明确提到该话题或引用该消息时才可涉及。"
)

BARE_WAKE_HINT = (
    "[裸唤醒]\n"
    "这是打招呼/点名，没有实质问题。"
    "开场或问一句意图即可，不要从旁人未完话题里选题作答。"
)


@dataclass
class AssembledContext:
    group_memory: str = ""
    user_profile: str = ""
    active_context: str = ""
    summary: str = ""
    current_message: str = ""
    entity_hint: str = ""
    resolved_text: str = ""
    bare_wake: bool = False
    turns: list[dict[str, Any]] = field(default_factory=list)

    def as_group_context_block(self) -> str:
        parts: list[str] = [SPEAKER_ISOLATION]
        if self.bare_wake:
            parts.append(BARE_WAKE_HINT)
        if self.group_memory:
            parts.append(f"[群记忆]\n{self.group_memory}")
        if self.user_profile:
            parts.append(f"[用户画像]\n{self.user_profile}")
        if self.summary:
            parts.append(f"[分片摘要]\n{self.summary}")
        if self.active_context:
            parts.append(f"[活跃上下文]\n{self.active_context}")
        if self.entity_hint:
            parts.append(self.entity_hint)
        if self.current_message:
            parts.append(f"[当前消息]\n{self.current_message}")
        return "\n\n".join(parts)


async def assemble_group_prompt(
    *,
    hot: HotStore,
    db: Any,
    platform: str,
    chat_id: str,
    user_id: str,
    sender_name: str,
    shard_id: str,
    text: str,
    bot_name: str = "小Lu",
    short_turns: int = SHORT_TURNS,
    query_embedding: list[float] | None = None,
    bare_wake: bool = False,
) -> AssembledContext:
    entity_stack = await hot.load_entity_stack(platform, chat_id)
    resolved, candidates = resolve_pronouns(text, entity_stack, current_user_id=user_id)
    entity_hint = build_entity_hint(candidates)

    turns = await hot.load_turns(
        platform=platform, chat_id=chat_id, user_id=user_id, shard_id=shard_id, limit=short_turns
    )
    if not turns and hasattr(db, "load_shard_turns"):
        turns = await db.load_shard_turns(shard_id, limit=short_turns)

    summary = await hot.get_summary(shard_id)
    if not summary and hasattr(db, "get_shard_summary"):
        summary = await db.get_shard_summary(shard_id)

    active_lines = [format_turn_line(t, bot_name=bot_name) for t in turns]
    active_context = "\n".join(line for line in active_lines if line)

    profile = ""
    if hasattr(db, "get_user_profile"):
        row = await db.get_user_profile(platform, user_id)
        if row:
            prefs = str(row.get("preferences") or "").strip()
            notes = str(row.get("notes") or "").strip()
            name = str(row.get("display_name") or sender_name or user_id).strip()
            bits = [f"当前说话人：{name}"]
            if prefs:
                bits.append(f"偏好：{prefs}")
            if notes:
                bits.append(f"备注：{notes}")
            profile = "；".join(bits)

    group_memory = ""
    events = await hot.load_group_mem(platform=platform, chat_id=chat_id, limit=20)
    if not events:
        events = await db.load_group_recent(platform, chat_id, limit=20)
    group_memory = format_group_context(
        events,
        current_user_id=user_id,
        current_name=sender_name,
        bot_name=bot_name,
        max_chars=1200,
    )
    if hasattr(db, "search_group_memory") and query_embedding is not None:
        hits = await db.search_group_memory(platform, chat_id, query_embedding, limit=5)
        if hits:
            seen = {str(e.get("content") or "") for e in events}
            extra_lines = []
            for h in hits:
                content = str(h.get("content") or "")
                if not content or content in seen:
                    continue
                extra_lines.append(
                    format_turn_line(
                        {
                            "role": h.get("role") or "user",
                            "sender_name": h.get("sender_name") or "",
                            "user_id": h.get("user_id") or "",
                            "content": content,
                        },
                        bot_name=bot_name,
                    )
                )
            if extra_lines:
                group_memory = (group_memory + "\n" + "\n".join(extra_lines)).strip() if group_memory else "\n".join(extra_lines)

    speaker = (sender_name or "").strip() or user_id
    current_message = f"{format_turn_line({'role': 'user', 'sender_name': speaker, 'user_id': user_id, 'content': resolved}, bot_name=bot_name)}"

    return AssembledContext(
        group_memory=group_memory,
        user_profile=profile,
        active_context=active_context,
        summary=summary or "",
        current_message=current_message,
        entity_hint=entity_hint,
        resolved_text=resolved,
        bare_wake=bare_wake,
        turns=turns,
    )

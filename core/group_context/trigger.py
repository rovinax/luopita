from __future__ import annotations

import re
from dataclasses import dataclass

from core.group_talk import (
    GroupReplyMode,
    decide_group_reply,
    looks_like_filler,
    mentioned_name,
)
from core.identity import mentioned_bot
from core.group_context.keys import SEMANTIC_TRIGGER_THRESHOLD, cosine_similarity

TriggerMode = GroupReplyMode

_COMMAND_PREFIXES = ("/", "!", "！", ".", "。")


@dataclass
class TriggerResult:
    mode: TriggerMode
    explicit: bool = False
    reason: str = ""
    replied_bot: bool = False
    semantic_score: float = 0.0


def has_command_prefix(text: str) -> bool:
    raw = (text or "").lstrip()
    if not raw:
        return False
    return raw.startswith(_COMMAND_PREFIXES)


def decide_trigger(
    *,
    channel_type: str,
    role: str,
    group_require_at: bool,
    text: str = "",
    mentioned: bool = False,
    named: bool = False,
    has_media: bool = False,
    engaged: bool = False,
    same_speaker: bool = False,
    can_open: bool = True,
    replies_left: int = 0,
    replied_bot: bool = False,
    command: bool = False,
    semantic_score: float = 0.0,
    semantic_threshold: float = SEMANTIC_TRIGGER_THRESHOLD,
) -> TriggerResult:
    """Layer 1: whether the bot should enter an active context."""
    if channel_type != "group" or not group_require_at:
        return TriggerResult(mode="direct", explicit=True, reason="private_or_open")

    if mentioned or named or replied_bot or command:
        return TriggerResult(
            mode="direct",
            explicit=True,
            reason="explicit",
            replied_bot=replied_bot,
            semantic_score=semantic_score,
        )

    if looks_like_filler(text) and not has_media:
        return TriggerResult(mode="ignore", reason="filler")

    if semantic_score >= semantic_threshold and can_open:
        return TriggerResult(
            mode="chime",
            explicit=False,
            reason="semantic",
            semantic_score=semantic_score,
        )

    mode = decide_group_reply(
        channel_type=channel_type,
        role=role,
        group_require_at=group_require_at,
        text=text,
        mentioned=False,
        named=False,
        has_media=has_media,
        engaged=engaged,
        same_speaker=same_speaker,
        can_open=can_open,
        replies_left=replies_left,
    )
    return TriggerResult(
        mode=mode,
        explicit=False,
        reason="heuristic" if mode != "ignore" else "ignore",
        semantic_score=semantic_score,
    )


def score_against_centroid(text: str, centroid: list[float] | None, embed_fn) -> float:
    if not centroid:
        return 0.0
    vec = embed_fn(text or "")
    return cosine_similarity(vec, centroid)


def extract_trigger_flags(
    *,
    text: str,
    at_user_ids: list[str],
    bot_id: str,
    wake_keywords: list[str] | None,
    persona_name: str,
) -> tuple[bool, bool, bool]:
    mentioned = mentioned_bot(at_user_ids, bot_id)
    named = mentioned_name(text, wake_keywords, persona_name)
    command = has_command_prefix(re.sub(r"\[CQ:[^\]]+\]", "", text or ""))
    return mentioned, named, command

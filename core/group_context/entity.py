from __future__ import annotations

import re
from typing import Any

_PRONOUN_RE = re.compile(r"(他|她|它|那个|那位|刚才那人|上面那人)")


def label_speaker(name: str, user_id: str = "") -> str:
    display = (name or "").strip() or (user_id or "").strip() or "某人"
    uid = (user_id or "").strip()
    if uid and uid not in display:
        return f"[{display}|{uid}]"
    return f"[{display}]"


def format_turn_line(turn: dict[str, Any], *, bot_name: str = "小Lu") -> str:
    role = str(turn.get("role") or "user")
    if role == "assistant":
        name = (bot_name or "小Lu").strip() or "小Lu"
        uid = ""
    else:
        name = str(turn.get("sender_name") or turn.get("user_id") or "某人")
        uid = str(turn.get("user_id") or "")
    text = " ".join(str(turn.get("content") or "").split())
    if len(text) > 240:
        text = text[:240] + "…"
    return f"{label_speaker(name, uid)}: {text}"


def resolve_pronouns(
    text: str,
    entity_stack: list[dict[str, Any]],
    *,
    current_user_id: str = "",
) -> tuple[str, list[dict[str, str]]]:
    """Rule-based coreference: bind 他/她/那个 to recent entities."""
    raw = text or ""
    if not _PRONOUN_RE.search(raw):
        return raw, []
    candidates: list[dict[str, str]] = []
    for item in entity_stack:
        uid = str(item.get("user_id") or "").strip()
        if not uid or uid == current_user_id:
            continue
        name = str(item.get("name") or uid).strip()
        candidates.append({"user_id": uid, "name": name})
        if len(candidates) >= 3:
            break
    if not candidates:
        return raw, []
    primary = candidates[0]
    note = f"（指代倾向：{primary['name']}|{primary['user_id']}）"
    if note in raw:
        return raw, candidates
    return f"{raw} {note}".strip(), candidates


def build_entity_hint(candidates: list[dict[str, str]]) -> str:
    if not candidates:
        return ""
    parts = [f"{c['name']}|{c['user_id']}" for c in candidates]
    return "近期可指代实体：" + "；".join(parts)

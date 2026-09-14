from __future__ import annotations

import re

from core.memory import MemoryService

_CORRECTION_RE = re.compile(
    r"(别|不要|别再|少|别用|别列|别说|太长|太啰嗦|别客服|别卖萌)([^。！？\n]{0,24})",
)
_KEEP_RE = re.compile(r"(继续|就这样|这样挺好|保持)([^。！？\n]{0,24})")


def should_reflect(user_text: str) -> bool:
    raw = (user_text or "").strip()
    if len(raw) < 4:
        return False
    return bool(_CORRECTION_RE.search(raw) or _KEEP_RE.search(raw))


def extract_reflect_notes(user_text: str) -> dict[str, str]:
    raw = (user_text or "").strip()
    keep = ""
    avoid = ""
    note = ""
    m = _CORRECTION_RE.search(raw)
    if m:
        avoid = " ".join(m.group(0).split())[:60]
    m = _KEEP_RE.search(raw)
    if m:
        keep = " ".join(m.group(0).split())[:60]
    if avoid or keep:
        note = " ".join(raw.split())[:80]
    return {"keep": keep, "avoid": avoid, "note": note}


def format_reflect_line(notes: dict[str, str]) -> str:
    bits = []
    if notes.get("avoid"):
        bits.append(f"避免：{notes['avoid']}")
    if notes.get("keep"):
        bits.append(f"保持：{notes['keep']}")
    if not bits and notes.get("note"):
        bits.append(notes["note"])
    return "；".join(bits)[:120]


async def maybe_reflect_turn(
    memory: MemoryService,
    *,
    user_id: str,
    user_text: str,
) -> str:
    if not should_reflect(user_text):
        return ""
    notes = extract_reflect_notes(user_text)
    line = format_reflect_line(notes)
    if not line:
        return ""
    await memory.remember_fact(user_id, f"策略：{line}")
    return line

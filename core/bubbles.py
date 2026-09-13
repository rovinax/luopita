from __future__ import annotations

import re

_SPLIT_RE = re.compile(r"\n\s*(?:-{2,}|\*{3,}|~~~)\s*\n")
_LINE_END_RE = re.compile(r"[。！？!?…~～）)】」』]$")
_MAX_BUBBLES = 4


def split_reply_bubbles(text: str, *, max_bubbles: int = _MAX_BUBBLES) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _SPLIT_RE.split(raw) if p.strip()]
    expanded: list[str] = []
    for part in parts:
        for para in _split_paragraphs(part):
            expanded.extend(_split_utterances(para))
    cleaned: list[str] = []
    for part in expanded:
        piece = part.strip().strip("-").strip()
        if piece:
            cleaned.append(piece)
    if not cleaned:
        return [raw]
    cap = max(1, int(max_bubbles or _MAX_BUBBLES))
    if len(cleaned) <= cap:
        return cleaned
    head = cleaned[: cap - 1]
    tail = "\n\n".join(cleaned[cap - 1 :])
    return head + [tail]


def _split_paragraphs(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_fence = False
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        if not in_fence and not stripped:
            piece = "\n".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
            continue
        buf.append(line)
    piece = "\n".join(buf).strip()
    if piece:
        parts.append(piece)
    return parts or [text.strip()]


def _is_complete_utterance(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return False
    if raw.startswith(("```", "|", "- ", "* ", "1.", "2.", "3.")):
        return False
    if _LINE_END_RE.search(raw):
        return True
    if raw.endswith(("，", "、", ",", "；", ";")):
        return False
    return len(raw) >= 8


def _split_utterances(text: str) -> list[str]:
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    if len(lines) < 2:
        return [text.strip()]
    if any(ln.startswith("```") for ln in lines):
        return [text.strip()]
    parts: list[str] = []
    buf: list[str] = []
    for line in lines:
        if buf and _is_complete_utterance(buf[-1]):
            parts.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        parts.append("\n".join(buf))
    return parts or [text.strip()]

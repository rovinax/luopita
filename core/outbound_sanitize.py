from __future__ import annotations

import re

# DeepSeek DSML / leaked tool-call markup that must never go out as chat bubbles.
_MARKERS = (
    "DSML",
    "dsml",
    "invoke name=",
    "parameter name=",
    "<|tool",
    "</|",
    "｜DSML｜",
    "｜dsml｜",
    "tool_calls",
    "tool call",
    "function_call",
)

# Match tags whether they use ASCII < > or fullwidth / special pipe forms.
_TAG_RE = re.compile(
    r"""(?:<\|[^|>]{0,80}\|>)|(?:</?\s*\|{0,4}\s*DSML[^>\n]{0,200}>)|(?:<[^>\n]{0,400}>)""",
    re.IGNORECASE,
)
_MESSAGE_PARAM_RE = re.compile(
    r"""parameter\s+name\s*=\s*["']message["'][^>]*>\s*(.*?)\s*</""",
    re.IGNORECASE | re.DOTALL,
)
_INVOKE_CHUNK_RE = re.compile(
    r"""(?:</?\s*\|{0,4}\s*DSML[^>\n]*?>)|(?:invoke\s+name\s*=\s*["'][^"']+["'])""",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)(?:```|$)", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+")
_QUOTE_RE = re.compile(r"(?m)^>\s?")
_LIST_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+\.)\s+")
_HR_RE = re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def looks_like_tool_markup(text: str) -> bool:
    raw = text or ""
    if not raw.strip():
        return False
    lowered = raw.lower()
    if any(marker.lower() in lowered for marker in _MARKERS):
        return True
    # Special-token forms that may not include the literal "DSML" ASCII spelling.
    if "invoke name=" in lowered and ("parameter name=" in lowered or "｜" in raw or "<|" in raw):
        return True
    return False


def strip_markdown(text: str) -> str:
    """Turn Markdown into plain text. Leaves [SILENCE] / CQ codes alone."""
    raw = text or ""
    if not raw:
        return ""
    raw = _FENCE_RE.sub(lambda match: (match.group(1) or "").strip("\n"), raw)
    raw = _IMAGE_RE.sub(r"\1", raw)
    raw = _LINK_RE.sub(_unwrap_link, raw)
    raw = _INLINE_CODE_RE.sub(r"\1", raw)
    raw = _BOLD_RE.sub(lambda match: next(g for g in match.groups() if g is not None), raw)
    raw = _ITALIC_RE.sub(r"\1", raw)
    raw = _ITALIC_UNDERSCORE_RE.sub(r"\1", raw)
    raw = _STRIKE_RE.sub(r"\1", raw)
    raw = _HEADING_RE.sub("", raw)
    raw = _QUOTE_RE.sub("", raw)
    raw = _HR_RE.sub("", raw)
    raw = _LIST_RE.sub("", raw)
    return raw


def sanitize_outbound_text(text: str) -> str:
    """Strip leaked tool markup and Markdown. Empty means 'do not send this'."""
    raw = (text or "").strip()
    if not raw:
        return ""
    had_markup = looks_like_tool_markup(raw)
    if had_markup:
        raw = _recover_from_tool_markup(raw)
        if not raw:
            return ""
    raw = strip_markdown(raw)
    raw = _normalize_plain(raw)
    if not raw:
        return ""
    if looks_like_tool_markup(raw):
        return ""
    if had_markup and len(re.sub(r"\W+", "", raw, flags=re.UNICODE)) < 2:
        return ""
    return raw


def _unwrap_link(match: re.Match[str]) -> str:
    label = (match.group(1) or "").strip()
    url = (match.group(2) or "").strip()
    if not url or url == label:
        return label
    if not label:
        return url
    return f"{label} {url}"


def _recover_from_tool_markup(raw: str) -> str:
    match = _MESSAGE_PARAM_RE.search(raw)
    if match:
        recovered = " ".join(match.group(1).split()).strip()
        recovered = _TAG_RE.sub("", recovered).strip()
        recovered = _INVOKE_CHUNK_RE.sub("", recovered).strip()
        if recovered and not looks_like_tool_markup(recovered):
            return recovered

    cleaned = _TAG_RE.sub("", raw)
    cleaned = _INVOKE_CHUNK_RE.sub("", cleaned)
    cleaned = re.sub(r"(?i)\bDSML\b", " ", cleaned)
    cleaned = re.sub(r"[｜|]{2,}", " ", cleaned)
    cleaned = re.sub(r"(?i)invoke\s+name\s*=\s*[\"'][^\"']+[\"']", " ", cleaned)
    cleaned = re.sub(r"(?i)parameter\s+name\s*=\s*[\"'][^\"']+[\"']", " ", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())
    return cleaned.strip()


def _normalize_plain(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

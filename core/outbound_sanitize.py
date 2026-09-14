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


def sanitize_outbound_text(text: str) -> str:
    """Remove leaked model tool-call markup. Empty means 'do not send this'."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if not looks_like_tool_markup(raw):
        return raw

    # Prefer recovering the human-facing message parameter if present.
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
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())
    cleaned = cleaned.strip()
    if not cleaned or looks_like_tool_markup(cleaned):
        return ""
    if len(re.sub(r"\W+", "", cleaned, flags=re.UNICODE)) < 2:
        return ""
    return cleaned

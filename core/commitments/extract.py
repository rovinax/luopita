from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.clock import now_shanghai
from core.cron.parse import parse_when
from core.cron.schedule import next_run_at
from core.cron.types import utc_iso


@dataclass
class ExtractedCommitment:
    action: str  # add | cancel
    text: str
    due_at: str = ""
    evidence: str = ""


_CANCEL_RE = re.compile(
    r"(算了|取消|不用了|别提醒|别跟进|做完了|已经做了|搞定了|完成了|不用提醒)",
    re.I,
)
_OPEN_RE = re.compile(
    r"(提醒我|记得|别忘|叫我|跟进一下|待办|todo|remind)",
    re.I,
)
_SCHEDULE_FRAG_RE = re.compile(
    r"(\d+\s*(?:分钟|分|小时|钟头|天)后|"
    r"明天(?:早上|上午|中午|下午|晚上)?\s*\d{0,2}\s*点?|"
    r"后天|"
    r"今晚|"
    r"每天\s*\d{1,2}\s*点|"
    r"\d+\s*[mh]|20m|1h)",
    re.I,
)


def due_from_text(text: str, *, now: datetime | None = None) -> str:
    clock = now_shanghai(now)
    raw = (text or "").strip()
    if not raw:
        return utc_iso(clock + timedelta(hours=1))

    for match in _SCHEDULE_FRAG_RE.finditer(raw):
        frag = match.group(1).strip()
        try:
            parsed, _ = parse_when(frag, now=clock)
            due = next_run_at(parsed.kind, parsed.schedule, now=clock)
            return utc_iso(due)
        except Exception:
            continue

    if "明天" in raw:
        due = (clock + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return utc_iso(due)
    if "后天" in raw:
        due = (clock + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
        return utc_iso(due)
    if "今晚" in raw:
        due = clock.replace(hour=20, minute=0, second=0, microsecond=0)
        if due <= clock:
            due = due + timedelta(days=1)
        return utc_iso(due)

    match = re.search(r"(\d+)\s*(分钟|分)", raw)
    if match:
        return utc_iso(clock + timedelta(minutes=int(match.group(1))))
    match = re.search(r"(\d+)\s*(小时|钟头)", raw)
    if match:
        return utc_iso(clock + timedelta(hours=int(match.group(1))))

    return utc_iso(clock + timedelta(hours=1))


def extract_commitment_ops(user_text: str, *, now: datetime | None = None) -> list[ExtractedCommitment]:
    raw = (user_text or "").strip()
    if not raw:
        return []
    ops: list[ExtractedCommitment] = []
    cancel = bool(_CANCEL_RE.search(raw))
    open_ask = bool(_OPEN_RE.search(raw) or ("提醒" in raw and re.search(r"明天|后天|今晚|\d+\s*分钟", raw)))

    if cancel and not open_ask:
        ops.append(ExtractedCommitment(action="cancel", text=raw[:80], evidence=raw[:120]))
        return ops

    if open_ask:
        text = re.sub(r"^(帮我|请|麻烦)", "", raw).strip()
        text = re.sub(r"^(提醒我|记得|别忘了?|叫我)", "", text).strip(" ，,")
        if not text:
            text = raw[:80]
        ops.append(
            ExtractedCommitment(
                action="add",
                text=text[:120],
                due_at=due_from_text(raw, now=now),
                evidence=raw[:160],
            )
        )
    elif cancel:
        ops.append(ExtractedCommitment(action="cancel", text=raw[:80], evidence=raw[:120]))
    return ops

from __future__ import annotations

from typing import Any

from core.clock import now_shanghai
from core.commitments.types import utc_iso

SCRATCHPAD_MAX = 200


def format_scratchpad_block(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    focus = str(data.get("focus") or "").strip()
    open_summary = str(data.get("open_summary") or "").strip()
    reflect = str(data.get("reflect_notes") or "").strip()
    lines: list[str] = []
    if focus:
        lines.append(f"焦点：{focus}")
    if open_summary:
        lines.append("开放承诺：")
        lines.append(open_summary)
    if reflect:
        lines.append(f"近期注意：{reflect}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > SCRATCHPAD_MAX:
        body = body[: SCRATCHPAD_MAX - 1].rstrip() + "…"
    return (
        "[当前工作态]\n"
        f"{body}\n"
        "这是你自己的短状态，不是记忆原文；接话时参考，不要复读整段。"
    )


async def load_scratchpad(db: Any, *, platform: str, user_id: str) -> dict[str, Any]:
    if not hasattr(db, "get_scratchpad"):
        return {}
    row = await db.get_scratchpad(platform, user_id)
    return dict(row or {})


async def save_scratchpad(
    db: Any,
    *,
    platform: str,
    user_id: str,
    focus: str = "",
    open_summary: str = "",
    reflect_notes: str = "",
    last_proactive_at: str = "",
) -> dict[str, Any]:
    existing = await load_scratchpad(db, platform=platform, user_id=user_id)
    payload = {
        "focus": (focus if focus is not None else existing.get("focus") or "")[:120],
        "open_summary": (open_summary if open_summary is not None else existing.get("open_summary") or "")[
            :180
        ],
        "reflect_notes": (
            reflect_notes if reflect_notes is not None else existing.get("reflect_notes") or ""
        )[:120],
        "last_proactive_at": last_proactive_at or existing.get("last_proactive_at") or "",
    }
    if hasattr(db, "put_scratchpad"):
        await db.put_scratchpad(platform, user_id, payload)
    return payload


async def touch_scratchpad_after_turn(
    db: Any,
    *,
    platform: str,
    user_id: str,
    user_text: str,
    open_summary: str = "",
    reflect_notes: str = "",
) -> dict[str, Any]:
    focus = " ".join((user_text or "").split())[:80]
    return await save_scratchpad(
        db,
        platform=platform,
        user_id=user_id,
        focus=focus,
        open_summary=open_summary,
        reflect_notes=reflect_notes,
    )


async def mark_proactive(db: Any, *, platform: str, user_id: str) -> None:
    existing = await load_scratchpad(db, platform=platform, user_id=user_id)
    await save_scratchpad(
        db,
        platform=platform,
        user_id=user_id,
        focus=str(existing.get("focus") or ""),
        open_summary=str(existing.get("open_summary") or ""),
        reflect_notes=str(existing.get("reflect_notes") or ""),
        last_proactive_at=utc_iso(now_shanghai()),
    )

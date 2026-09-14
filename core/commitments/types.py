from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

CommitmentStatus = Literal["open", "done", "cancelled"]


def utc_iso(dt: datetime | None = None) -> str:
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _as_iso(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return utc_iso(value)
    return str(value)


@dataclass
class Commitment:
    id: str
    text: str
    status: CommitmentStatus = "open"
    due_at: str = ""
    platform: str = ""
    channel_type: str = ""
    chat_id: str = ""
    user_id: str = ""
    source_session: str = ""
    evidence: str = ""
    last_notified_at: str = ""
    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "due_at": self.due_at,
            "platform": self.platform,
            "channel_type": self.channel_type,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "source_session": self.source_session,
            "evidence": self.evidence,
            "last_notified_at": self.last_notified_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> Commitment | None:
        if not row:
            return None
        status = str(row.get("status") or "open").strip() or "open"
        if status not in {"open", "done", "cancelled"}:
            status = "open"
        return cls(
            id=str(row.get("id") or ""),
            text=str(row.get("text") or "").strip(),
            status=status,  # type: ignore[arg-type]
            due_at=_as_iso(row.get("due_at")),
            platform=str(row.get("platform") or ""),
            channel_type=str(row.get("channel_type") or ""),
            chat_id=str(row.get("chat_id") or ""),
            user_id=str(row.get("user_id") or ""),
            source_session=str(row.get("source_session") or ""),
            evidence=str(row.get("evidence") or ""),
            last_notified_at=_as_iso(row.get("last_notified_at")),
            created_at=_as_iso(row.get("created_at")) or utc_iso(),
            updated_at=_as_iso(row.get("updated_at")) or utc_iso(),
        )

    def owned_by(self, *, platform: str, user_id: str) -> bool:
        return self.platform == (platform or "") and self.user_id == (user_id or "")

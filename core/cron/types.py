from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

CronKind = Literal["at", "every", "cron"]

MAX_CONSECUTIVE_FAILURES = 5
MISSED_GRACE_SEC = 300
MIN_INTERVAL_SEC = 1
MAX_CONCURRENT_RUNS = 2


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


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "on"}:
        return True
    if text in {"0", "false", "f", "no", "off", ""}:
        return False
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ParsedSchedule:
    kind: CronKind
    schedule: str
    delete_after_run: bool
    label: str = ""


@dataclass
class CronJob:
    id: str
    name: str = ""
    enabled: bool = True
    kind: CronKind = "at"
    schedule: str = ""
    tz: str = "Asia/Shanghai"
    prompt: str = ""
    platform: str = ""
    channel_type: str = ""
    chat_id: str = ""
    user_id: str = ""
    delete_after_run: bool = False
    next_run_at: str = ""
    last_run_at: str = ""
    last_status: str = ""
    last_error: str = ""
    consecutive_failures: int = 0
    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "kind": self.kind,
            "schedule": self.schedule,
            "tz": self.tz,
            "prompt": self.prompt,
            "platform": self.platform,
            "channel_type": self.channel_type,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "delete_after_run": self.delete_after_run,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> CronJob | None:
        if not row:
            return None
        kind = str(row.get("kind") or "at").strip() or "at"
        if kind not in {"at", "every", "cron"}:
            kind = "at"
        return cls(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            enabled=_as_bool(row.get("enabled"), True),
            kind=kind,  # type: ignore[arg-type]
            schedule=str(row.get("schedule") or ""),
            tz=str(row.get("tz") or "Asia/Shanghai") or "Asia/Shanghai",
            prompt=str(row.get("prompt") or ""),
            platform=str(row.get("platform") or ""),
            channel_type=str(row.get("channel_type") or ""),
            chat_id=str(row.get("chat_id") or ""),
            user_id=str(row.get("user_id") or ""),
            delete_after_run=_as_bool(row.get("delete_after_run"), False),
            next_run_at=_as_iso(row.get("next_run_at")),
            last_run_at=_as_iso(row.get("last_run_at")),
            last_status=str(row.get("last_status") or ""),
            last_error=str(row.get("last_error") or ""),
            consecutive_failures=_as_int(row.get("consecutive_failures"), 0),
            created_at=_as_iso(row.get("created_at")) or utc_iso(),
            updated_at=_as_iso(row.get("updated_at")) or utc_iso(),
        )

    def owned_by(self, *, platform: str, user_id: str) -> bool:
        return self.platform == (platform or "") and self.user_id == (user_id or "")

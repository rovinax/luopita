from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def now_shanghai(dt: datetime | None = None) -> datetime:
    if dt is None:
        return datetime.now(SHANGHAI)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)


def format_now(dt: datetime | None = None) -> str:
    local = now_shanghai(dt)
    weekday = WEEKDAYS[local.weekday()]
    return f"现在是北京时间 {local:%Y-%m-%d} {weekday} {local:%H:%M}（UTC+8）"


def parse_created_at(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return now_shanghai(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHANGHAI)


def format_hhmm(value: object) -> str:
    local = parse_created_at(value)
    if local is None:
        return ""
    return local.strftime("%H:%M")


def format_shanghai_display(value: object) -> str:
    local = parse_created_at(value)
    if local is None:
        return str(value or "").strip()
    return f"{local:%Y-%m-%d %H:%M}（UTC+8）"

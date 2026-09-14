from __future__ import annotations

import re
from datetime import datetime, timedelta

from core.clock import now_shanghai
from core.cron.types import ParsedSchedule

_CRON_FIELD = r"[0-9*,/\-?]+"
_CRON5_RE = re.compile(
    rf"^({_CRON_FIELD})\s+({_CRON_FIELD})\s+({_CRON_FIELD})\s+({_CRON_FIELD})\s+({_CRON_FIELD})(?=\s|$)"
)
_ISO_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?)"
)
_CLOCK_RE = re.compile(r"^(\d{1,2})[:：](\d{2})(?=\s|$)")
_EVERY_EN_RE = re.compile(
    r"^(?:every|each)\s+(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b",
    re.I,
)
_EVERY_CN_RE = re.compile(
    r"^每(?:隔)?\s*(\d+)\s*(秒|秒钟|分钟|分|小时|钟头|天)"
)
_EVERY_HOUR_RE = re.compile(r"^(?:每小时|每个小时|每钟头)\b")
_RELATIVE_EN_RE = re.compile(
    r"^(?:in\s+)?(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b",
    re.I,
)
_RELATIVE_CN_RE = re.compile(
    r"^(\d+)\s*(秒|秒钟|分钟|分|小时|钟头|天)后"
)
_BARE_DURATION_RE = re.compile(
    r"^(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|秒|秒钟|分钟|分|小时|钟头|天)\b",
    re.I,
)
_PERIOD = r"(早上|上午|凌晨|中午|下午|晚上|傍晚|夜里|今晚)?"
_DAILY_RE = re.compile(
    rf"^(?:每天|每日|天天)\s*{_PERIOD}\s*(\d{{1,2}})\s*(?:[:：点时](\d{{1,2}})?)?(?=\s|$)"
)
_WEEKLY_RE = re.compile(
    rf"^(?:每周|每星期|每个星期|周|星期)([一二三四五六日天0-6])\s*{_PERIOD}\s*"
    rf"(\d{{1,2}})\s*(?:[:：点时](\d{{1,2}})?)?(?=\s|$)"
)
_DAY_CLOCK_RE = re.compile(
    rf"^(今天|明天)\s*{_PERIOD}\s*(\d{{1,2}})\s*(?:[:：点时](\d{{1,2}})?)?(?=\s|$)"
)

_WEEKDAY_NUM = {
    "0": 0,
    "7": 0,
    "日": 0,
    "天": 0,
    "1": 1,
    "一": 1,
    "2": 2,
    "二": 2,
    "3": 3,
    "三": 3,
    "4": 4,
    "四": 4,
    "5": 5,
    "五": 5,
    "6": 6,
    "六": 6,
}

_SEC_UNITS = {"s", "sec", "secs", "second", "seconds", "秒", "秒钟"}
_MIN_UNITS = {"m", "min", "mins", "minute", "minutes", "分", "分钟"}
_HOUR_UNITS = {"h", "hr", "hrs", "hour", "hours", "小时", "钟头"}
_DAY_UNITS = {"d", "day", "days", "天"}


def _unit_seconds(unit: str) -> int:
    key = (unit or "").strip().lower()
    if key in _SEC_UNITS:
        return 1
    if key in _MIN_UNITS:
        return 60
    if key in _HOUR_UNITS:
        return 3600
    if key in _DAY_UNITS or key == "日":
        return 86400
    raise ValueError(f"unknown duration unit: {unit}")


def duration_seconds(text: str) -> int:
    raw = (text or "").strip()
    match = _BARE_DURATION_RE.match(raw)
    if not match or match.group(0).strip() != raw:
        raise ValueError(f"not a duration: {text}")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("duration must be positive")
    return amount * _unit_seconds(match.group(2))


def canonical_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _adjust_hour(hour: int, period: str | None) -> int:
    if hour < 0 or hour > 24:
        raise ValueError("hour out of range")
    if hour == 24:
        hour = 0
    tag = (period or "").strip()
    if tag in {"晚上", "傍晚", "夜里", "今晚"} and 1 <= hour <= 11:
        return hour + 12
    if tag in {"下午"} and 1 <= hour <= 11:
        return hour + 12
    if tag in {"中午"} and hour == 0:
        return 12
    if tag in {"早上", "上午", "凌晨"} and hour == 12:
        return 0
    if hour == 24:
        return 0
    return hour


def _minute(raw: str | None) -> int:
    if raw is None or raw == "":
        return 0
    value = int(raw)
    if value < 0 or value > 59:
        raise ValueError("minute out of range")
    return value


def _rest(text: str, match: re.Match[str]) -> str:
    return (text[match.end() :] or "").strip()


def _at_duration(seconds: int, label: str = "") -> ParsedSchedule:
    canon = canonical_duration(seconds)
    return ParsedSchedule(kind="at", schedule=canon, delete_after_run=True, label=label or f"{canon} 后")


def _every_duration(seconds: int, label: str = "") -> ParsedSchedule:
    canon = canonical_duration(seconds)
    return ParsedSchedule(kind="every", schedule=canon, delete_after_run=False, label=label or f"每 {canon}")


def _cron_daily(hour: int, minute: int) -> ParsedSchedule:
    expr = f"{minute} {hour} * * *"
    return ParsedSchedule(
        kind="cron",
        schedule=expr,
        delete_after_run=False,
        label=f"每天 {hour:02d}:{minute:02d}",
    )


def _cron_weekly(weekday: int, hour: int, minute: int) -> ParsedSchedule:
    expr = f"{minute} {hour} * * {weekday}"
    names = "日一二三四五六"
    label = f"每周{names[weekday]} {hour:02d}:{minute:02d}"
    return ParsedSchedule(kind="cron", schedule=expr, delete_after_run=False, label=label)


def _at_datetime(dt: datetime, label: str = "") -> ParsedSchedule:
    stamp = dt.isoformat()
    pretty = dt.strftime("%Y-%m-%d %H:%M")
    return ParsedSchedule(
        kind="at",
        schedule=stamp,
        delete_after_run=True,
        label=label or pretty,
    )


def parse_when(text: str, *, now: datetime | None = None) -> tuple[ParsedSchedule, str] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    clock = now_shanghai(now)

    match = _CRON5_RE.match(raw)
    if match:
        expr = " ".join(match.group(i) for i in range(1, 6))
        parsed = ParsedSchedule(kind="cron", schedule=expr, delete_after_run=False, label=expr)
        return parsed, _rest(raw, match)

    match = _EVERY_EN_RE.match(raw)
    if match:
        seconds = int(match.group(1)) * _unit_seconds(match.group(2))
        return _every_duration(seconds), _rest(raw, match)

    match = _EVERY_CN_RE.match(raw)
    if match:
        seconds = int(match.group(1)) * _unit_seconds(match.group(2))
        return _every_duration(seconds), _rest(raw, match)

    match = _EVERY_HOUR_RE.match(raw)
    if match:
        return _every_duration(3600, "每小时"), _rest(raw, match)

    match = _RELATIVE_CN_RE.match(raw)
    if match:
        seconds = int(match.group(1)) * _unit_seconds(match.group(2))
        return _at_duration(seconds), _rest(raw, match)

    match = _DAILY_RE.match(raw)
    if match:
        hour = _adjust_hour(int(match.group(2)), match.group(1))
        minute = _minute(match.group(3))
        return _cron_daily(hour, minute), _rest(raw, match)

    match = _WEEKLY_RE.match(raw)
    if match:
        weekday = _WEEKDAY_NUM.get(match.group(1))
        if weekday is None:
            return None
        hour = _adjust_hour(int(match.group(3)), match.group(2))
        minute = _minute(match.group(4))
        return _cron_weekly(weekday, hour, minute), _rest(raw, match)

    match = _DAY_CLOCK_RE.match(raw)
    if match:
        day_word, period, hour_s, minute_s = match.group(1), match.group(2), match.group(3), match.group(4)
        hour = _adjust_hour(int(hour_s), period)
        minute = _minute(minute_s)
        target = clock.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day_word == "明天":
            target = target + timedelta(days=1)
        elif target <= clock:
            target = target + timedelta(days=1)
        return _at_datetime(target), _rest(raw, match)

    match = _ISO_RE.match(raw)
    if match:
        stamp = match.group(1).strip()
        parsed_dt = _parse_iso(stamp, clock)
        if parsed_dt is not None:
            return _at_datetime(parsed_dt), raw[match.end() :].strip()

    match = _CLOCK_RE.match(raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target = clock.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= clock:
                target = target + timedelta(days=1)
            return _at_datetime(target), _rest(raw, match)

    match = _RELATIVE_EN_RE.match(raw)
    if match:
        seconds = int(match.group(1)) * _unit_seconds(match.group(2))
        return _at_duration(seconds), _rest(raw, match)

    match = _BARE_DURATION_RE.match(raw)
    if match:
        seconds = int(match.group(1)) * _unit_seconds(match.group(2))
        return _at_duration(seconds), _rest(raw, match)

    return None


def _parse_iso(stamp: str, clock: datetime) -> datetime | None:
    text = stamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = text + f"T{clock:%H:%M:%S}"
    text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return now_shanghai(parsed)


CRON_SUBCOMMANDS = {
    "list": "list",
    "ls": "list",
    "add": "add",
    "create": "add",
    "new": "add",
    "get": "get",
    "show": "get",
    "rm": "rm",
    "remove": "rm",
    "delete": "rm",
    "del": "rm",
    "on": "on",
    "enable": "on",
    "off": "off",
    "disable": "off",
    "run": "run",
    "now": "run",
    "help": "help",
    "?": "help",
}


def split_cron_args(args: str) -> tuple[str, str]:
    raw = (args or "").strip()
    if not raw:
        return "list", ""
    first, _, rest = raw.partition(" ")
    key = first.lower()
    mapped = CRON_SUBCOMMANDS.get(key)
    if mapped:
        return mapped, rest.strip()
    if parse_when(raw) is not None:
        return "add", raw
    return "help", raw

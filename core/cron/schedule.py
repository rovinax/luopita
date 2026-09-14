from __future__ import annotations

from datetime import datetime, timedelta

from core.clock import now_shanghai, parse_created_at
from core.cron.parse import canonical_duration, duration_seconds, parse_when
from core.cron.types import CronKind, MIN_INTERVAL_SEC, ParsedSchedule


def next_run_at(
    kind: CronKind | str,
    schedule: str,
    *,
    now: datetime | None = None,
) -> datetime:
    clock = now_shanghai(now)
    if kind == "at":
        return _next_at(schedule, clock)
    if kind == "every":
        seconds = max(MIN_INTERVAL_SEC, duration_seconds(schedule))
        return clock + timedelta(seconds=seconds)
    if kind == "cron":
        return next_cron(schedule, clock)
    raise ValueError(f"unknown schedule kind: {kind}")


def _next_at(schedule: str, clock: datetime) -> datetime:
    text = (schedule or "").strip()
    if not text:
        raise ValueError("empty at schedule")
    parsed = parse_created_at(text)
    if parsed is not None and _looks_like_datetime(text):
        return parsed
    try:
        seconds = max(MIN_INTERVAL_SEC, duration_seconds(text))
    except ValueError:
        found = parse_when(text, now=clock)
        if found is None:
            raise ValueError(f"cannot parse at schedule: {schedule}") from None
        spec, _ = found
        return next_run_at(spec.kind, spec.schedule, now=clock)
    return clock + timedelta(seconds=seconds)


def _looks_like_datetime(text: str) -> bool:
    raw = (text or "").strip()
    return raw[:1].isdigit() and "-" in raw[:11] and len(raw) >= 10


def parse_cron_fields(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError(f"cron needs 5 fields: {expr}")
    minutes = _parse_field(parts[0], 0, 59)
    hours = _parse_field(parts[1], 0, 23)
    doms = _parse_field(parts[2], 1, 31)
    months = _parse_field(parts[3], 1, 12)
    dows = _parse_field(parts[4], 0, 7)
    if 7 in dows:
        dows.add(0)
        dows.discard(7)
    star_dom = parts[2] in {"*", "?"}
    star_dow = parts[4] in {"*", "?"}
    return minutes, hours, doms, months, dows, star_dom, star_dow


def _parse_field(expr: str, min_v: int, max_v: int) -> set[int]:
    values: set[int] = set()
    raw = (expr or "").strip()
    if not raw:
        raise ValueError("empty cron field")
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        step = 1
        if "/" in token:
            body, step_s = token.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"bad cron step: {token}")
        else:
            body = token
        if body in {"*", "?"}:
            start, end = min_v, max_v
        elif "-" in body:
            a, b = body.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(body)
        if start > end:
            start, end = end, start
        start = max(min_v, start)
        end = min(max_v, end)
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"empty cron field: {expr}")
    return values


def next_cron(expr: str, now: datetime) -> datetime:
    minutes, hours, doms, months, dows, star_dom, star_dow = parse_cron_fields(expr)
    clock = now_shanghai(now).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = clock + timedelta(days=400)

    def cron_dow(dt: datetime) -> int:
        return (dt.weekday() + 1) % 7

    cursor = clock
    while cursor < limit:
        if cursor.month in months and cursor.hour in hours and cursor.minute in minutes:
            dom_ok = cursor.day in doms
            dow_ok = cron_dow(cursor) in dows
            if star_dom and star_dow:
                ok = True
            elif star_dom:
                ok = dow_ok
            elif star_dow:
                ok = dom_ok
            else:
                ok = dom_ok or dow_ok
            if ok:
                return cursor
        cursor += timedelta(minutes=1)
    raise ValueError(f"no upcoming slot for cron: {expr}")


def describe_schedule(kind: str, schedule: str) -> str:
    if kind == "at":
        parsed = parse_created_at(schedule)
        if parsed is not None and _looks_like_datetime(schedule):
            return f"一次 {parsed.strftime('%Y-%m-%d %H:%M')}"
        return f"一次 {schedule} 后"
    if kind == "every":
        return f"每 {schedule}"
    return schedule


def format_run_at(value: str) -> str:
    parsed = parse_created_at(value)
    if parsed is None:
        return "-"
    return parsed.strftime("%Y-%m-%d %H:%M")


def resolve_schedule(
    *,
    kind: str = "",
    schedule: str = "",
    prompt: str = "",
    now: datetime | None = None,
) -> tuple[ParsedSchedule, str]:
    when = (schedule or "").strip()
    body = (prompt or "").strip()
    if kind in {"at", "every", "cron"} and when:
        if kind == "cron":
            parse_cron_fields(when)
            spec = ParsedSchedule(kind="cron", schedule=when, delete_after_run=False, label=when)
        elif kind == "every":
            seconds = duration_seconds(when)
            spec = ParsedSchedule(
                kind="every",
                schedule=canonical_duration(seconds),
                delete_after_run=False,
                label=f"每 {when}",
            )
        else:
            found = parse_when(when, now=now)
            if found and found[0].kind == "at" and not found[1]:
                spec = found[0]
            else:
                spec = ParsedSchedule(kind="at", schedule=when, delete_after_run=True, label=when)
        return spec, body
    blob = " ".join(part for part in (when, body) if part).strip()
    found = parse_when(blob, now=now)
    if found is None:
        raise ValueError("看不懂这个时间")
    spec, rest = found
    return spec, rest or body

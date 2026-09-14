from __future__ import annotations

import re

from core.clock import format_now
from core.cron.schedule import describe_schedule, format_run_at
from core.cron.types import CronJob
from core.group_talk import strip_wake_noise

_CMD_RE = re.compile(r"^/([a-zA-Z0-9_-]+)(?:\s+(.*))?$")
_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")

# canonical name -> short description for /help
CATALOG: dict[str, str] = {
    "help": "看命令",
    "ping": "在不在",
    "status": "模型、数据库、NapCat",
    "time": "北京时间",
    "whoami": "你的身份",
    "model": "当前模型",
    "allow": "shell 白名单",
    "clear": "忘掉这轮对话",
    "cron": "定时任务",
}

ALIASES: dict[str, str] = {
    "h": "help",
    "?": "help",
    "stat": "status",
    "date": "time",
    "now": "time",
    "id": "whoami",
    "me": "whoami",
    "reset": "clear",
    "forget": "clear",
    "cmds": "allow",
    "job": "cron",
}


def parse_slash(
    text: str,
    *,
    keywords: list[str] | None = None,
    persona_name: str = "",
) -> tuple[str, str] | None:
    raw = _CQ_RE.sub(" ", text or "")
    raw = strip_wake_noise(raw, keywords=keywords, persona_name=persona_name)
    if raw == "/":
        return "help", ""
    match = _CMD_RE.match(raw)
    if not match:
        return None
    name = match.group(1).lower()
    args = (match.group(2) or "").strip()
    return ALIASES.get(name, name), args


def render_help() -> str:
    lines = ["主人命令，只有 / 开头算："]
    for name, desc in CATALOG.items():
        lines.append(f"/{name}  {desc}")
    return "\n".join(lines)


def render_unknown(name: str) -> str:
    return f"没有 `/{name}`。发 /help 看可用命令。"


def render_ping() -> str:
    return "在。"


def render_time() -> str:
    return format_now()


def render_whoami(
    *,
    nickname: str,
    platform: str,
    user_id: str,
    channel_type: str,
    chat_id: str,
    bot_id: str,
) -> str:
    name = nickname.strip() or "主人"
    where = "群" if channel_type == "group" else "私聊"
    bot = f" · bot {bot_id}" if bot_id else ""
    return f"{name} · {platform} {user_id} · {where} {chat_id}{bot}"


def render_model(*, provider: str, model: str, vision_model: str) -> str:
    vision = f" · 视觉 {vision_model}" if vision_model and vision_model != model else ""
    return f"{provider} / {model}{vision}"


def render_allow(allowlist: list[str]) -> str:
    items = [c.strip() for c in allowlist if c and str(c).strip()]
    if not items:
        return "shell 白名单是空的。"
    return "shell 白名单：" + "、".join(items)


def render_status(
    *,
    provider: str,
    model: str,
    database: str,
    db_ok: bool,
    redis: str,
    redis_ok: bool,
    napcat_on: bool,
    bot_id: str,
) -> str:
    db = "ok" if db_ok else "down"
    rd = "ok" if redis_ok else "down"
    napcat = "开" if napcat_on else "关"
    bot = f" {bot_id}" if bot_id else ""
    return (
        f"模型 {provider}/{model}\n"
        f"db {database} {db} · redis {redis} {rd}\n"
        f"napcat {napcat}{bot}"
    )


def render_cleared() -> str:
    return "这轮对话我忘掉了。"


def render_cron_usage() -> str:
    return (
        "主人定时任务：\n"
        "/cron  列表\n"
        "/cron add 20m 提醒喝水\n"
        "/cron add every 1h 查天气\n"
        "/cron add 每天8:00 早安\n"
        "/cron get <id>\n"
        "/cron on|off <id>\n"
        "/cron run <id>\n"
        "/cron rm <id>"
    )


def render_cron_missing() -> str:
    return "没有这个任务。"


def render_cron_empty() -> str:
    return "还没有定时任务。/cron add 20m 提醒喝水"


def render_cron_job(job: CronJob) -> str:
    flag = "开" if job.enabled else "停"
    when = describe_schedule(job.kind, job.schedule)
    nxt = format_run_at(job.next_run_at)
    name = job.name.strip()
    title = f"{job.id}  {flag}  {when}"
    if name and name != (job.prompt or "")[:24]:
        title = f"{title}  {name}"
    lines = [title, f"下次 {nxt}", (job.prompt or "").strip() or "（没有内容）"]
    if job.last_status:
        extra = job.last_status
        if job.last_error:
            extra = f"{extra} · {job.last_error}"
        lines.append(f"上次 {extra}")
    return "\n".join(lines)


def render_cron_list(jobs: list[CronJob]) -> str:
    if not jobs:
        return render_cron_empty()
    return "\n\n".join(render_cron_job(job) for job in jobs)


def render_cron_added(job: CronJob) -> str:
    return "记下了。\n" + render_cron_job(job)


def render_cron_removed(job_id: str) -> str:
    return f"删掉了 `{job_id}`。"


def render_cron_ran(job: CronJob | None, job_id: str) -> str:
    if job is None:
        return f"`{job_id}` 已经跑完并清掉了。"
    return "立刻跑了一轮。\n" + render_cron_job(job)


def render_cron_error(message: str) -> str:
    return (message or "").strip() or "设不了。"

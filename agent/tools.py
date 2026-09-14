from __future__ import annotations

from typing import Callable

from langchain_core.tools import BaseTool, tool

from agent.napcat_tools import build_napcat_tools
from core.agent_runtime import CommandPolicy, execute_command
from core.clock import format_now
from core.commands import (
    render_cron_added,
    render_cron_error,
    render_cron_job,
    render_cron_list,
    render_cron_missing,
    render_cron_ran,
    render_cron_removed,
)
from core.cron.context import current_cron_delivery
from core.identity import OWNER_REFUSAL, is_owner_role
from interface.platform.napcat import NapcatAdapter
from utils.log import ChatbotLogger


def build_tools(
    get_policy: Callable[[], CommandPolicy],
    logger: ChatbotLogger | None = None,
    napcat: NapcatAdapter | None = None,
    role: str = "owner",
    cron: object | None = None,
) -> list[BaseTool]:
    log = logger or ChatbotLogger()

    @tool
    def get_current_date() -> str:
        """Return the current Beijing time: date, weekday, and clock (UTC+8)."""
        return format_now()

    @tool
    def run_shell(command: str) -> str:
        """Run one whitelisted program (not bash) and return STDOUT/STDERR."""
        if not is_owner_role():
            return OWNER_REFUSAL
        return execute_command(payload=command, policy=get_policy(), logger=log)

    allowed = ", ".join(sorted(get_policy().allow)) or "(none)"
    run_shell.description = (
        "Run one local program and return STDOUT/STDERR. "
        "This is not bash: no pipes, redirects, ';', '&&', or '$()'. "
        f"Allowed programs right now: {allowed}. "
        "One program per call. Do not call the same command twice in one turn. "
        "If a previous turn said the command was not allowed "
        "but it now appears in the allowed list, call it once. "
        "Example: curl -sS https://wttr.in/Chongqing"
    )

    tools: list[BaseTool] = [get_current_date]
    if role == "owner":
        tools.append(run_shell)
        tools.extend(build_napcat_tools(napcat))
        if cron is not None:
            tools.append(build_cron_tool(cron))
    return tools


def build_cron_tool(service) -> BaseTool:
    @tool
    async def cron(
        action: str,
        kind: str = "",
        schedule: str = "",
        prompt: str = "",
        name: str = "",
        job_id: str = "",
        enabled: bool | None = None,
    ) -> str:
        """Manage owner-only scheduled jobs for reminders and recurring tasks."""
        if not is_owner_role():
            return OWNER_REFUSAL
        act = (action or "").strip().lower()
        delivery = current_cron_delivery()
        platform = delivery.platform if delivery else ""
        user_id = delivery.user_id if delivery else ""
        try:
            if act in {"list", "ls"}:
                jobs = await service.list_jobs(user_id=user_id, platform=platform)
                return render_cron_list(jobs)
            if act in {"add", "create"}:
                if delivery is None:
                    return render_cron_error("现在不知道往哪投递，换 /cron 设。")
                job = await service.add(
                    kind=kind,
                    schedule=schedule,
                    prompt=prompt,
                    name=name,
                    platform=delivery.platform,
                    channel_type=delivery.channel_type,
                    chat_id=delivery.chat_id,
                    user_id=delivery.user_id,
                )
                return render_cron_added(job)
            sid = (job_id or "").strip()
            if act in {"get", "show"}:
                job = await service.owned(sid, platform=platform, user_id=user_id) if sid else None
                return render_cron_job(job) if job else render_cron_missing()
            if act in {"remove", "rm", "delete"}:
                job = await service.owned(sid, platform=platform, user_id=user_id) if sid else None
                if job is None:
                    return render_cron_missing()
                await service.remove(job.id)
                return render_cron_removed(job.id)
            if act in {"enable", "on", "disable", "off"}:
                job = await service.owned(sid, platform=platform, user_id=user_id) if sid else None
                if job is None:
                    return render_cron_missing()
                turn_on = act in {"enable", "on"} if enabled is None else bool(enabled)
                updated = await service.set_enabled(job.id, turn_on)
                return render_cron_job(updated or job)
            if act == "update":
                job = await service.owned(sid, platform=platform, user_id=user_id) if sid else None
                if job is None:
                    return render_cron_missing()
                updated = await service.update(
                    job.id,
                    kind=kind,
                    schedule=schedule,
                    prompt=prompt,
                    name=name,
                    enabled=enabled,
                )
                return render_cron_job(updated or job)
            if act in {"run", "now"}:
                job = await service.owned(sid, platform=platform, user_id=user_id) if sid else None
                if job is None:
                    return render_cron_missing()
                await service.run_now(job.id)
                leftover = await service.get(job.id)
                return render_cron_ran(leftover, job.id)
            return render_cron_error(
                "cron 的 action 用 list / get / add / update / remove / run / enable / disable。"
            )
        except ValueError as exc:
            return render_cron_error(str(exc))
        except Exception as exc:
            return render_cron_error(str(exc))

    cron.description = (
        "Create and manage scheduled jobs for the owner. "
        "Use this for reminders and recurring work; never sleep or poll. "
        "Actions: list, get, add, update, remove, run, enable, disable. "
        "For add, pass kind (at|every|cron) and schedule "
        "(20m, 1h, '0 8 * * *', or Chinese like 每天8点 / 10分钟后) plus prompt. "
        "Jobs fire as a separate agent turn and the reply is sent back to this chat."
    )
    return cron

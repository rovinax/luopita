from __future__ import annotations

from typing import Callable

from langchain_core.tools import BaseTool, tool

from agent.napcat_tools import build_napcat_tools
from core.agent_runtime import CommandPolicy, execute_command
from core.clock import format_now
from core.identity import OWNER_REFUSAL, is_owner_role
from interface.platform.napcat import NapcatAdapter
from utils.log import ChatbotLogger


def build_tools(
    get_policy: Callable[[], CommandPolicy],
    logger: ChatbotLogger | None = None,
    napcat: NapcatAdapter | None = None,
    role: str = "owner",
) -> list[BaseTool]:
    log = logger or ChatbotLogger()

    @tool
    def get_current_date() -> str:
        """Return the current Beijing time: date, weekday, and clock (UTC+8)."""
        return format_now()

    @tool
    def run_shell(command: str) -> str:
        """Run a whitelisted local shell command and return STDOUT/STDERR."""
        if not is_owner_role():
            return OWNER_REFUSAL
        return execute_command(payload=command, policy=get_policy(), logger=log)

    allowed = ", ".join(sorted(get_policy().allow)) or "(none)"
    run_shell.description = (
        "Run a local shell command and return STDOUT/STDERR. "
        f"Allowed programs right now: {allowed}. "
        "Do not call the same command twice in one turn. "
        "If a previous turn said the command was not allowed "
        "but it now appears in the allowed list, call it once. "
        "Example: curl -sS wttr.in/Chongqing"
    )

    tools: list[BaseTool] = [get_current_date]
    if role == "owner":
        tools.append(run_shell)
        tools.extend(build_napcat_tools(napcat))
    return tools

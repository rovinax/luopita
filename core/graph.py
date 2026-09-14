from __future__ import annotations

from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import BaseMessage, RemoveMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from agent.tools import build_tools
from core.agent_runtime import CommandPolicy
from core.media import messages_have_vision, prepare_messages_for_llm
from core.memory import MemoryService
from core.window import (
    MAX_TOOL_ROUNDS,
    SHORT_TERM_MAX,
    drop_trailing_extra_humans,
    tool_rounds_since_last_human,
    trim_short_term,
)
from interface.llm.factory import get_chat_model, vision_model_name
from utils.config import AppConfig
from utils.log import ChatbotLogger

GRAPH_RECURSION_LIMIT = 16
INVOKE_TIMEOUT_SEC = 90


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def policy_from_config(cfg: AppConfig) -> CommandPolicy:
    import os

    workdir = os.path.abspath(cfg.agent.workdir or ".")
    return CommandPolicy(
        allow=set(cfg.agent.command_allowlist),
        workdir=workdir,
        timeout_sec=cfg.agent.timeout_sec,
    )


def build_graph(
    cfg: AppConfig,
    logger: ChatbotLogger,
    checkpointer: Any,
    get_config: Callable[[], AppConfig],
    napcat: Any = None,
    role: str = "owner",
    memory: MemoryService | None = None,
    cron: Any = None,
    include_cron: bool = True,
):
    tools = build_tools(
        lambda: policy_from_config(get_config()),
        logger=logger,
        napcat=napcat,
        role=role,
        cron=cron if include_cron else None,
    )
    text_model = get_chat_model(
        provider=cfg.llm.provider,
        base_url=cfg.llm.base_url,
        api_key=cfg.llm.api_key,
        model=cfg.llm.model,
    )
    vision_model = get_chat_model(
        provider=cfg.llm.provider,
        base_url=cfg.llm.base_url,
        api_key=cfg.llm.api_key,
        model=vision_model_name(cfg.llm.provider, cfg.llm.model, cfg.llm.vision_model),
    )
    text_bound = text_model.bind_tools(tools) if tools else text_model
    vision_bound = vision_model.bind_tools(tools) if tools else vision_model
    tool_node = ToolNode(tools)

    async def call_model(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        conf = dict(config.get("configurable") or {})
        persona = conf.get("persona") or get_config().persona.system_prompt
        memories = conf.get("memories") or ""
        context = conf.get("qq_context") or ""
        group_context = conf.get("group_context") or ""
        system = persona
        if context:
            system = f"{system}\n\n{context}"
        if group_context:
            system = f"{system}\n\n{group_context}"
        if memories:
            system = f"{system}\n\n{memories}"
        raw_messages = list(state.get("messages") or [])
        cap = get_config().agent.short_term_messages or SHORT_TERM_MAX
        keep, dropped = trim_short_term(raw_messages, max_messages=cap)
        parked: list[BaseMessage] = []
        if str(conf.get("channel_type") or "") == "group":
            keep, parked = drop_trailing_extra_humans(keep)
        if dropped and memory is not None and str(conf.get("role") or role) == "owner":
            try:
                await memory.archive_dropped(str(conf.get("user_id") or ""), dropped)
            except Exception as exc:
                logger.warning(f"long-term archive skip: {exc}")
        state_messages = prepare_messages_for_llm(keep)
        messages = [SystemMessage(content=system), *state_messages]
        use_vision = messages_have_vision(state_messages)
        if tool_rounds_since_last_human(keep) >= MAX_TOOL_ROUNDS:
            bound = vision_model if use_vision else text_model
        else:
            bound = vision_bound if use_vision else text_bound
        response = await bound.ainvoke(messages)
        removals = [
            RemoveMessage(id=message.id)
            for message in [*dropped, *parked]
            if getattr(message, "id", None)
        ]
        return {"messages": [*removals, response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


def make_memory_checkpointer() -> MemorySaver:
    return MemorySaver()

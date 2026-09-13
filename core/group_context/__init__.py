"""Group chat five-layer context: trigger → route → assemble → compress → store."""

from __future__ import annotations

from core.group_context.assembler import AssembledContext, assemble_group_prompt
from core.group_context.pipeline import GroupContextPipeline
from core.group_context.router import RouteResult
from core.group_context.trigger import TriggerResult, decide_trigger

__all__ = [
    "AssembledContext",
    "GroupContextPipeline",
    "RouteResult",
    "TriggerResult",
    "assemble_group_prompt",
    "decide_trigger",
]

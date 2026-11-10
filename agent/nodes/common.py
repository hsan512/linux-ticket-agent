"""Helpers shared by nodes."""

from __future__ import annotations

from agent.llm import Usage
from agent.state import TraceEntry


def llm_trace(node: str, usage: Usage, **extra) -> TraceEntry:
    """Trace entry for a node that made one LLM call; `extra` adds node-specific
    fields."""
    return TraceEntry(
        node=node,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cost_usd=usage.cost_usd,
        latency_s=usage.latency_s,
        error=None,
        **extra,
    )

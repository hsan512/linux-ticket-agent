"""Summaries of a ticket's trace, shared by the evaluation runs and the API's log
lines."""

from __future__ import annotations

from collections import defaultdict

from agent.config import GENERATOR_MODEL
from agent.state import AgentState


def summarize(state: AgentState, wall_s: float) -> dict:
    """Aggregate the trace into usage, cost, and per-node wall latency. Tool entries are
    skipped for latency because tools run concurrently; their batch wall time is in
    `tools_wall_s`."""
    trace = state["trace"]
    llm = [t for t in trace if "model" in t]
    latency: dict[str, float] = defaultdict(float)
    for t in trace:
        if "tool" not in t:
            latency[t["node"]] += t.get("latency_s", 0.0) + t.get("tools_wall_s", 0.0)
    return {
        "usage": {
            "model": GENERATOR_MODEL,
            "input_tokens": sum(t["input_tokens"] for t in llm),
            "output_tokens": sum(t["output_tokens"] for t in llm),
            "reasoning_tokens": sum(t["reasoning_tokens"] for t in llm),
            "llm_calls": len(llm),
            "tool_calls": [t["tool"] for t in trace if "tool" in t],
            # includes calls skipped for exceeding max_tool_calls; the trace says which
            "tool_errors": sum(1 for t in trace if "tool" in t and t.get("error")),
        },
        "cost_usd": sum(t.get("cost_usd", 0.0) for t in trace),
        "latency_s": dict(latency) | {"total": wall_s},
    }

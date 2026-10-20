"""finalize: mark the ticket answered (no LLM call)."""

from __future__ import annotations

from agent.state import AgentState, TraceEntry


def finalize(state: AgentState) -> dict:
    return {"status": "answered", "trace": [TraceEntry(node="finalize", latency_s=0.0, cost_usd=0.0, error=None)]}

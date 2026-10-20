"""escalate: stop and hand off to a human, keeping the draft and evidence for them (no LLM call)."""

from __future__ import annotations

from agent.escalation import escalation_reason
from agent.state import AgentState, TraceEntry


def evidence_insufficient(state: AgentState) -> bool:
    """grade_evidence's final verdict; False when grading is off (no verdict to act on)."""
    return state.get("evidence_sufficient") is False


def escalate(state: AgentState) -> dict:
    reason = escalation_reason(state["draft"].confidence, evidence_insufficient(state))
    return {"status": "escalated", "escalation_reason": reason,
            "trace": [TraceEntry(node="escalate", latency_s=0.0, cost_usd=0.0, error=None)]}

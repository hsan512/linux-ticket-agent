"""escalate: stop and hand off to a human, keeping the draft and evidence for them (no
LLM call)."""

from __future__ import annotations

from agent.escalation import escalation_reason
from agent.nodes.validate import BLOCKING
from agent.state import AgentState, EscalationKind, TraceEntry
from agent.tools.validate import Issue, format_issues


def evidence_insufficient(state: AgentState) -> bool:
    """grade_evidence's final verdict; False when grading is off (no verdict to act
    on)."""
    return state.get("evidence_sufficient") is False


def validation_reason(
    issues: list[Issue], error: str | None, revise_attempts: int
) -> str:
    """Why validate handed off: the check itself failed, or blocking issues survived the
    revisions."""
    if error:
        return f"validation could not run: {error}"
    blocking = [issue for issue in issues if issue.kind in BLOCKING]
    return (
        f"blocking issues left after {revise_attempts} revisions:\n"
        f"{format_issues(blocking)}"
    )


def escalate(state: AgentState) -> dict:
    """Record who handed off and why: validate, or the confidence/evidence rule after
    draft. `escalation_kind` is the category; `escalation_reason` the detail behind
    it."""
    kind: EscalationKind
    if state.get("decision_after_validate") == "escalate":
        kind = "unchecked" if state["validation_error"] else "unsafe"
        reason = validation_reason(
            state["validation"],
            state["validation_error"],
            state.get("revise_attempts", 0),
        )
    else:
        kind = "not_confident"
        reason = escalation_reason(
            state["draft"].confidence, evidence_insufficient(state)
        )
    return {
        "status": "escalated",
        "escalation_kind": kind,
        "escalation_reason": reason,
        "trace": [TraceEntry(node="escalate", latency_s=0.0, cost_usd=0.0, error=None)],
    }

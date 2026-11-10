"""validate: check the draft's commands (syntax, command names, danger) before it is
shown. No LLM."""

from __future__ import annotations

import time
from collections import Counter

from langgraph.runtime import Runtime

from agent.state import AgentContext, AgentState, TraceEntry
from agent.tools.validate import Issue, validate_commands

BLOCKING = frozenset(
    {"syntax", "catastrophic"}
)  # must not reach the asker: fixed, or escalated


def decision_after_validate(
    issues: list[Issue],
    error: str | None,
    revise_attempts: int,
    max_revise_attempts: int,
) -> str:
    """Next node after validate: "revise", "finalize", or "escalate".

    - check failed (`error`): escalate, an unchecked answer is never shown as if it had
      passed
    - syntax / catastrophic: revise while attempts are left, then escalate
    - anything else: finalize, which warns about unknown_command and destructive
      commands

    unknown_command never causes a revision: on the dev run all 5 were false alarms and
    one revision broke a working command (eval/results/findings.md, item 28).
    """
    if error:
        return "escalate"
    issue_kinds = [issue.kind for issue in issues]

    if BLOCKING.intersection(issue_kinds):
        if revise_attempts < max_revise_attempts:
            return "revise"
        else:
            return "escalate"
    return "finalize"


def validate(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    """Check the draft's commands and decide the next node; writes validation,
    validation_error, and decision_after_validate (read by the graph's conditional
    edge)."""
    ctx = runtime.context
    commands = state["draft"].commands
    revise_attempts = state.get("revise_attempts", 0)
    max_revise_attempts = ctx.max_revise_attempts
    start = time.perf_counter()
    try:
        issues = validate_commands(commands, ctx.command_lookup, state["ticket"])
        error = None
    except Exception as exc:  # e.g. bash -n timing out: recorded, never swallowed
        issues, error = [], repr(exc)
    decision = decision_after_validate(
        issues, error, revise_attempts, max_revise_attempts
    )
    trace = TraceEntry(
        node="validate",
        cost_usd=0.0,
        latency_s=time.perf_counter() - start,
        error=error,
        commands=len(commands),
        issue_counts=dict(Counter(issue.kind for issue in issues)),
    )
    # A check that could not run must not look like a check that found nothing: routing
    # reads validation_error and does not show an unchecked answer as if it had passed.
    return {
        "validation": issues,
        "validation_error": error,
        "decision_after_validate": decision,
        "trace": [trace],
    }

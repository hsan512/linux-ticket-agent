"""finalize: mark the ticket answered and write what the asker sees (no LLM call).

Warnings about commands are added by code, so they do not depend on the model. They go
in `final_answer` only: `draft` stays as generated, which is what eval/judge.py scores.
"""

from __future__ import annotations

from agent.state import AgentState, TraceEntry
from agent.tools.validate import Issue, format_issues

WARNING = (
    "**Warning: check these commands before running them, they can destroy data.**"
)
UNKNOWN_NOTE = (
    "**Note: these commands were not found in the Ubuntu package index. They may come "
    "from another distribution or source, so check that they exist on your system.**"
)
HEADINGS = {
    "destructive": WARNING,
    "unknown_command": UNKNOWN_NOTE,
}  # shown in this order


def with_warnings(answer: str, issues: list[Issue]) -> str:
    """`answer` with its risky commands listed first, so the asker reads them before any
    step."""
    groups = {
        kind: [issue for issue in issues if issue.kind == kind] for kind in HEADINGS
    }
    warnings = [
        f"{HEADINGS[kind]}\n{format_issues(found)}"
        for kind, found in groups.items()
        if found
    ]
    return "\n\n".join([*warnings, answer])


def finalize(state: AgentState) -> dict:
    return {
        "status": "answered",
        "final_answer": with_warnings(
            state["draft"].answer, state.get("validation", [])
        ),
        "trace": [TraceEntry(node="finalize", latency_s=0.0, cost_usd=0.0, error=None)],
    }

"""finalize: mark the ticket answered and write what the asker sees (no LLM call).

Warnings for destructive commands are added by code, so they do not depend on the model. They go
in `final_answer` only: `draft` stays as generated, which is what eval/judge.py scores.
"""

from __future__ import annotations

from agent.state import AgentState, TraceEntry
from agent.tools.validate import Issue, format_issues

WARNING = "**Warning: check these commands before running them, they can destroy data.**"


def with_warnings(answer: str, issues: list[Issue]) -> str:
    """`answer` with its destructive commands listed first, so the asker reads them before any step."""
    destructive = [issue for issue in issues if issue.kind == "destructive"]
    return f"{WARNING}\n{format_issues(destructive)}\n\n{answer}" if destructive else answer


def finalize(state: AgentState) -> dict:
    return {"status": "answered", "final_answer": with_warnings(state["draft"].answer, state.get("validation", [])),
            "trace": [TraceEntry(node="finalize", latency_s=0.0, cost_usd=0.0, error=None)]}

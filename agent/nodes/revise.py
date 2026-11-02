"""revise: rewrite the draft to fix the problems validate found (LLM + Pydantic)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from agent import prompts
from agent.config import GENERATOR_MODEL
from agent.llm import invoke_structured
from agent.nodes.common import llm_trace
from agent.nodes.validate import BLOCKING
from agent.schemas import AgentDraft
from agent.state import AgentState
from agent.tools.retrieval import format_context
from agent.tools.validate import format_issues

from collections import Counter


def revise(state: AgentState) -> dict:
    draft = state["draft"]
    ticket = state["ticket"]
    evidence = state.get("evidence", [])
    # the other commands stay in the answer as they are: finalize warns about them
    issues = [issue for issue in state.get("validation", []) if issue.kind in BLOCKING]
    issue_counts = dict(Counter(issue.kind for issue in issues))
    revise_attempts = state.get("revise_attempts", 0)
    user = (f"Ticket: {ticket}\n\n---\nPrevious answer: {draft.answer}\n\n"
            f"Previous commands:\n{"\n".join(f"- {c}" for c in draft.commands)}\n\n"
            f"Context passages:\n{format_context(evidence)}\n\n---\n"
            f"Previous citations:\n{", ".join(draft.citations)}\n\n"
            f"Problems found:\n{format_issues(issues)}")
    result = invoke_structured(GENERATOR_MODEL, AgentDraft, [SystemMessage(prompts.REVISE), HumanMessage(user)])
    return {"draft": result.parsed, "revise_attempts": revise_attempts + 1,
            "revisions": [{"draft": draft, "issues": issues}],
            "trace": [llm_trace("revise", result, issue_counts=issue_counts)]}

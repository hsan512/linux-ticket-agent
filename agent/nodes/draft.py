"""draft: compose the answer with citations and a confidence level (LLM + Pydantic)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from agent import prompts
from agent.config import GENERATOR_MODEL
from agent.llm import invoke_structured
from agent.nodes.common import llm_trace
from agent.schemas import AgentDraft
from agent.state import AgentState
from agent.tools.retrieval import format_context


def draft(state: AgentState) -> dict:
    c = state["classification"]
    evidence = state.get("evidence", [])
    context = (f"Context passages:\n\n{format_context(evidence)}" if evidence
               else "No context passages were retrieved.")
    user = (f"Ticket classification: issue_type={c.issue_type}, risk_level={c.risk_level}\n\n"
            f"{context}\n\n---\nTicket:\n{state['ticket']}")
    result = invoke_structured(GENERATOR_MODEL, AgentDraft, [SystemMessage(prompts.DRAFT), HumanMessage(user)])
    return {"draft": result.parsed, "trace": [llm_trace("draft", result)]}

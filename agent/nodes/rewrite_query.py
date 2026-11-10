"""rewrite_query: plan new searches using the earlier queries and what the grader said
is missing. The planned calls are executed by the next retrieve step."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from agent import prompts
from agent.config import GENERATOR_MODEL
from agent.llm import invoke_tools
from agent.nodes.common import llm_trace
from agent.nodes.retrieve import TOOLS
from agent.state import AgentState


def rewrite_query(state: AgentState) -> dict:
    tried = "\n".join(f"- {q}" for q in state.get("queries_tried", [])) or "- (none)"
    user = (
        f"Ticket:\n{state['ticket']}\n\n---\nEarlier searches:\n{tried}\n\n"
        f"Still missing: {state.get('grade_feedback') or 'unspecified'}"
    )
    plan = invoke_tools(
        GENERATOR_MODEL, TOOLS, [SystemMessage(prompts.REWRITE), HumanMessage(user)]
    )
    calls = plan.message.tool_calls if plan.message else []
    return {
        "planned_calls": [{"name": c["name"], "args": c["args"]} for c in calls],
        "trace": [llm_trace("rewrite_query", plan, tool_calls_requested=len(calls))],
    }

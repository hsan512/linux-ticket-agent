"""classify: issue type, risk level, and missing information (LLM + Pydantic)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from agent import prompts
from agent.config import GENERATOR_MODEL
from agent.llm import invoke_structured
from agent.nodes.common import llm_trace
from agent.schemas import Classification
from agent.state import AgentState


def classify(state: AgentState) -> dict:
    result = invoke_structured(
        GENERATOR_MODEL,
        Classification,
        [SystemMessage(prompts.CLASSIFY), HumanMessage(f"Ticket:\n{state['ticket']}")],
    )
    return {"classification": result.parsed, "trace": [llm_trace("classify", result)]}

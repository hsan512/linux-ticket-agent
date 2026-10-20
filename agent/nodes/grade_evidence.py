"""grade_evidence: keep only passages an LLM judges relevant, decide if they suffice, and route.

Cosine scores do not separate useful from useless passages on this benchmark (see
eval/results/findings.md, item 3), so grading is done by the LLM, not by a score threshold.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent import prompts
from agent.config import GENERATOR_MODEL
from agent.llm import invoke_structured
from agent.nodes.common import llm_trace
from agent.nodes.retrieve import renumber
from agent.schemas import Evidence, EvidenceGrade
from agent.state import AgentContext, AgentState, TraceEntry
from agent.tools.retrieval import format_context


def keep_relevant(evidence: list[Evidence], grade: EvidenceGrade) -> list[Evidence]:
    """Passages graded relevant, renumbered. A passage the grader skipped counts as irrelevant."""
    relevant_ids = {g.id for g in grade.grades if g.relevant}
    return renumber([e for e in evidence if e.id in relevant_ids])


def next_step(sufficient: bool, attempts: int, max_retrievals: int) -> str:
    if sufficient or attempts >= max_retrievals:
        return "draft"
    return "rewrite_query"


def grade_evidence(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    evidence = state.get("evidence", [])
    attempts = state.get("retrieval_attempts", 0)
    if not evidence:
        entry = TraceEntry(node="grade_evidence", kept=0, dropped=0, sufficient=False, latency_s=0.0,
                           cost_usd=0.0, error=None)
        return {"grade_feedback": "No passages were retrieved.", "trace": [entry], "evidence_sufficient": False,
                "next_step": next_step(False, attempts, runtime.context.max_retrievals)}

    user = f"Ticket:\n{state['ticket']}\n\n---\nPassages:\n\n{format_context(evidence)}"
    result = invoke_structured(GENERATOR_MODEL, EvidenceGrade, [SystemMessage(prompts.GRADE), HumanMessage(user)])
    grade = result.parsed
    kept = keep_relevant(evidence, grade)
    sufficient = grade.sufficient and bool(kept)
    return {
        "evidence": kept,
        "grade_feedback": grade.missing,
        "evidence_sufficient": sufficient,
        "next_step": next_step(sufficient, attempts, runtime.context.max_retrievals),
        "trace": [llm_trace("grade_evidence", result, kept=len(kept), dropped=len(evidence) - len(kept),
                            sufficient=sufficient)],
    }

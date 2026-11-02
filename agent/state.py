"""LangGraph state and runtime context for the troubleshooting agent."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from agent.schemas import AgentDraft, Classification, Evidence
from agent.tools.packages import CommandPackageLookup
from agent.tools.retrieval import Retriever
from agent.tools.validate import Issue

Status = Literal["answered", "needs_info", "escalated"]


class TraceEntry(TypedDict, total=False):
    """One step of the per-ticket log: a node run or a single tool execution. Fields are optional;
    each node fills the ones that apply to it."""

    node: str
    tool: str  # set for tool executions (search_docs / search_similar / check_command)
    args: dict
    model: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float
    latency_s: float
    results: int  # passages returned by a tool call
    tool_calls_requested: int  # on the retrieve/rewrite_query LLM entry
    tools_wall_s: float  # wall time of the (concurrent) tool batch, on the retrieve entry
    kept: int  # grade_evidence: passages judged relevant
    dropped: int  # grade_evidence: passages judged irrelevant
    sufficient: bool  # grade_evidence verdict
    commands: int  # validate: command items checked
    issue_counts: dict[str, int]  # issues per kind: found by validate, or sent to be fixed by revise
    error: str | None


class AgentState(TypedDict, total=False):
    """Per-ticket blackboard. Each node returns a partial update: plain fields are overwritten,
    Annotated[..., operator.add] fields are appended to."""

    ticket: str
    classification: Classification
    evidence: list[Evidence]  # after grade_evidence: only passages judged relevant
    retrieval_attempts: int
    planned_calls: list[dict]  # searches planned by rewrite_query, executed by the next retrieve
    queries_tried: Annotated[list[str], operator.add]
    grade_feedback: str  # what grade_evidence says is still missing
    evidence_sufficient: bool  # grade_evidence's latest verdict
    next_step: str  # routing decision written by grade_evidence
    escalation_reason: str
    draft: AgentDraft
    validation: list[Issue]  # problems the validate node found in the draft's commands
    validation_error: str | None  # set when the check itself failed: the draft is unchecked, not clean
    revise_attempts: int  # how many times the draft has been revised after validation issues
    revisions: Annotated[list[dict], operator.add]  # {"draft", "issues"} each revise replaced, oldest first
    decision_after_validate: str  # "revise", "finalize", or "escalate" after validate
    final_answer: str  # after finalize: the answer to show the user (draft.answer + warnings)
    status: Status
    trace: Annotated[list[TraceEntry], operator.add]  # every node appends; never overwritten


@dataclass
class AgentContext:
    """Dependencies and limits, injected per run via LangGraph's context_schema."""

    retriever: Retriever
    command_lookup: CommandPackageLookup
    max_tool_calls: int = 6  # per retrieve step
    tool_retries: int = 2  # extra attempts per failed tool call
    max_retrievals: int = 2  # retrieve rounds per ticket (1 = no query rewriting)
    max_revise_attempts: int = 2  # after validate finds issues, how many times to revise the draft before escalating

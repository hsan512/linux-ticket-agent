"""The troubleshooting agent as a LangGraph state graph.

Features are switched on one at a time so each can be measured (CLAUDE.md section 7,
ablation):

    v1  classify -> retrieve -> draft -> finalize
    +grade      classify -> retrieve -> grade_evidence -> draft -> finalize
                                 ^           | insufficient, rounds left
                                 +-- rewrite_query <-+

    +escalate   ... -> draft -> escalate (rule fires) | finalize (answered)

    +validate   ... -> draft -> validate -> finalize (clean or warnings only)
                                   ^   | -> escalate (unchecked, or blocking issues)
                                   |   v
                                   revise (fixable issues, tries left)

With both, the rule is checked first, on the first draft: a ticket it escalates is not
validated. Validate's own escalation does not depend on a rule being set. ask_user is
not wired because the classifier never reports missing info on this benchmark
(eval/results/findings.md, item 6).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.escalation import RULES, should_escalate
from agent.nodes.classify import classify
from agent.nodes.draft import draft
from agent.nodes.escalate import escalate, evidence_insufficient
from agent.nodes.finalize import finalize
from agent.nodes.grade_evidence import grade_evidence
from agent.nodes.retrieve import retrieve
from agent.nodes.revise import revise
from agent.nodes.rewrite_query import rewrite_query
from agent.nodes.validate import validate
from agent.state import AgentContext, AgentState

MAX_STEPS = 16  # LangGraph recursion_limit: hard cap on node executions per ticket


@dataclass(frozen=True)
class Features:
    """Which optional parts of the graph are switched on; `version` names the run in
    reports."""

    grade_evidence: bool = False  # grade_evidence + rewrite_query loop
    escalation_rule: str | None = (
        None  # a key of agent.escalation.RULES; None = no such rule
    )
    validate_revise: bool = False  # validate + revise loop

    @property
    def version(self) -> str:
        if self.validate_revise:
            rule = f"_{self.escalation_rule}" if self.escalation_rule else ""
            return f"agent_v4_validate{rule}" + (
                "" if self.grade_evidence else "_nograde"
            )
        if self.escalation_rule:
            return f"agent_v3_escalate_{self.escalation_rule}" + (
                "" if self.grade_evidence else "_nograde"
            )
        return "agent_v2_grade" if self.grade_evidence else "agent_v1_minimal"


def route_after_draft(
    rule: str, should_validate: bool = False
) -> Callable[[AgentState], str]:
    """Routing function for the edge after draft: "escalate" if `rule` fires, otherwise
    the next step, "validate" or "finalize"."""

    def route(state: AgentState) -> str:
        escalate_it = should_escalate(
            rule, state["draft"].confidence, evidence_insufficient(state)
        )
        return (
            "escalate" if escalate_it else "validate" if should_validate else "finalize"
        )

    return route


def build_graph(features: Features = Features()) -> CompiledStateGraph:
    """Compile the graph for `features`; raises ValueError for an unknown escalation
    rule."""
    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("classify", classify)
    g.add_node("retrieve", retrieve)
    g.add_node("draft", draft)
    g.add_node("finalize", finalize)
    g.add_edge(START, "classify")
    g.add_edge("classify", "retrieve")
    if features.grade_evidence:
        g.add_node("grade_evidence", grade_evidence)
        g.add_node("rewrite_query", rewrite_query)
        g.add_edge("retrieve", "grade_evidence")
        g.add_conditional_edges(
            "grade_evidence", lambda s: s["next_step"], ["draft", "rewrite_query"]
        )
        g.add_edge("rewrite_query", "retrieve")
    else:
        g.add_edge("retrieve", "draft")
    after_draft = "validate" if features.validate_revise else "finalize"
    if features.escalation_rule:
        if features.escalation_rule not in RULES:
            raise ValueError(
                f"unknown escalation rule {features.escalation_rule!r}; choose from "
                f"{sorted(RULES)}"
            )
        route = route_after_draft(features.escalation_rule, features.validate_revise)
        g.add_conditional_edges("draft", route, ["escalate", after_draft])
    else:
        g.add_edge("draft", after_draft)
    if features.validate_revise:
        g.add_node("validate", validate)
        g.add_node("revise", revise)
        g.add_conditional_edges(
            "validate",
            lambda s: s["decision_after_validate"],
            ["revise", "finalize", "escalate"],
        )
        g.add_edge("revise", "validate")
    if features.validate_revise or features.escalation_rule:
        g.add_node("escalate", escalate)
        g.add_edge("escalate", END)
    g.add_edge("finalize", END)
    return g.compile(name=features.version)


def run_ticket(
    graph: CompiledStateGraph, ticket: str, context: AgentContext
) -> AgentState:
    """Run one ticket to the end; the returned state is the final one (status, draft,
    trace...)."""
    return graph.invoke(
        {"ticket": ticket, "trace": []},
        config={"recursion_limit": MAX_STEPS},
        context=context,
    )

"""The troubleshooting agent as a LangGraph state graph.

Features are switched on one at a time so each can be measured (CLAUDE.md section 7, ablation):

    v1  classify -> retrieve -> draft -> finalize
    +grade      classify -> retrieve -> grade_evidence -> draft -> finalize
                                 ^           | insufficient, rounds left
                                 +-- rewrite_query <-+

    +escalate   ... -> draft -> escalate (rule fires: hand off to a human) | finalize (answered)

Still to come (build step 4): validate/revise. ask_user is not wired because the classifier
never reports missing info on this benchmark (eval/results/findings.md, item 6).
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
from agent.nodes.rewrite_query import rewrite_query
from agent.state import AgentContext, AgentState

MAX_STEPS = 16  # LangGraph recursion_limit: hard cap on node executions per ticket


@dataclass(frozen=True)
class Features:
    grade_evidence: bool = False  # grade_evidence + rewrite_query loop
    escalation_rule: str | None = None  # a key of agent.escalation.RULES; None = always answer

    @property
    def version(self) -> str:
        if self.escalation_rule:
            return f"agent_v3_escalate_{self.escalation_rule}" + ("" if self.grade_evidence else "_nograde")
        return "agent_v2_grade" if self.grade_evidence else "agent_v1_minimal"


def route_after_draft(rule: str) -> Callable[[AgentState], str]:
    def route(state: AgentState) -> str:
        escalate_it = should_escalate(rule, state["draft"].confidence, evidence_insufficient(state))
        return "escalate" if escalate_it else "finalize"
    return route


def build_graph(features: Features = Features()) -> CompiledStateGraph:
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
        g.add_conditional_edges("grade_evidence", lambda s: s["next_step"], ["draft", "rewrite_query"])
        g.add_edge("rewrite_query", "retrieve")
    else:
        g.add_edge("retrieve", "draft")
    if features.escalation_rule:
        if features.escalation_rule not in RULES:
            raise ValueError(f"unknown escalation rule {features.escalation_rule!r}; choose from {sorted(RULES)}")
        g.add_node("escalate", escalate)
        g.add_conditional_edges("draft", route_after_draft(features.escalation_rule), ["escalate", "finalize"])
        g.add_edge("escalate", END)
    else:
        g.add_edge("draft", "finalize")
    g.add_edge("finalize", END)
    return g.compile(name=features.version)


def run_ticket(graph: CompiledStateGraph, ticket: str, context: AgentContext) -> AgentState:
    return graph.invoke({"ticket": ticket, "trace": []}, config={"recursion_limit": MAX_STEPS}, context=context)

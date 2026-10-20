"""Unit tests for agent pieces that need no API calls: tool execution, evidence numbering,
trace aggregation, and graph wiring."""


from types import SimpleNamespace

import pytest

from agent.graph import Features, build_graph, route_after_draft
from agent.nodes.escalate import escalate
from agent.nodes.grade_evidence import keep_relevant, next_step
from agent.nodes.retrieve import execute_tool_calls, renumber, retrieve, run_tool
from agent.schemas import AgentDraft, Evidence, EvidenceGrade, PassageGrade
from agent.state import AgentContext
from agent.tools.packages import CommandPackageLookup
from eval.run_agent import summarize


def ev(source: str, url: str, text: str = "t") -> Evidence:
    return Evidence(id="X", source=source, title="T", url=url, text=text, score=0.5)


class FakeRetriever:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls: list[tuple[str, str]] = []

    def search_docs(self, query: str) -> list[Evidence]:
        self.calls.append(("docs", query))
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("qdrant down")
        return [ev("docs", f"d/{query}")]

    def search_similar(self, query: str) -> list[Evidence]:
        self.calls.append(("history", query))
        return [ev("history", f"h/{query}")]


LOOKUP = CommandPackageLookup({"noble": {"ls": ["coreutils"], "nc.openbsd": ["netcat-openbsd"]}})


def call(name: str, query: str) -> dict:
    return {"name": name, "args": {"query": query}, "id": "c"}


def check(command: str) -> dict:
    return {"name": "CheckCommand", "args": {"command": command}, "id": "c"}


def test_renumber_dedupes_and_numbers_per_source() -> None:
    out = renumber([ev("docs", "a"), ev("package", "p"), ev("history", "b"), ev("docs", "a"), ev("docs", "c"),
                    ev("package", "q")])
    assert [(e.id, e.url) for e in out] == [("D1", "a"), ("P1", "p"), ("H1", "b"), ("D2", "c"), ("P2", "q")]


def test_execute_tool_calls_runs_requested_tools() -> None:
    r = FakeRetriever()
    evidence, trace = execute_tool_calls([call("SearchDocs", "rsync"), call("SearchSimilar", "copy")], r, LOOKUP, 4, 0)
    assert [e.id for e in evidence] == ["D1", "H1"]
    assert [t["tool"] for t in trace] == ["search_docs", "search_similar"]
    assert all(t["error"] is None and t["results"] == 1 for t in trace)


def test_no_tool_calls_means_no_evidence() -> None:
    evidence, trace = execute_tool_calls([], FakeRetriever(), LOOKUP, 4, 0)
    assert evidence == [] and trace == []


def test_tool_errors_are_retried_then_logged() -> None:
    r = FakeRetriever(fail_times=1)
    evidence, trace = execute_tool_calls([call("SearchDocs", "x")], r, LOOKUP, 4, retries=1)
    assert len(evidence) == 1 and trace[0]["error"] is None and len(r.calls) == 2

    r = FakeRetriever(fail_times=5)
    evidence, trace = execute_tool_calls([call("SearchDocs", "x")], r, LOOKUP, 4, retries=1)
    assert evidence == [] and "qdrant down" in trace[0]["error"]


def test_tool_call_cap_and_unknown_tool_are_logged() -> None:
    calls = [call("SearchDocs", str(i)) for i in range(3)] + [call("Bogus", "y")]
    evidence, trace = execute_tool_calls(calls, FakeRetriever(), LOOKUP, max_calls=2, retries=0)
    assert len(evidence) == 2
    assert "skipped" in trace[2]["error"] and "skipped" in trace[3]["error"]  # cap is checked before the name
    _, trace = execute_tool_calls([call("Bogus", "y")], FakeRetriever(), LOOKUP, 4, 0)
    assert "unknown tool" in trace[0]["error"] and trace[0]["tool"] == "Bogus"  # logged under the model's name


def test_check_command_tool() -> None:
    r = FakeRetriever()
    evidence, trace = execute_tool_calls([check("nc"), call("SearchDocs", "x"), check("awk")], r, LOOKUP, 4, 0)
    assert [e.id for e in evidence] == ["P1", "D1", "P2"]
    assert "netcat-openbsd" in evidence[0].text and "not found" in evidence[2].text
    assert [t["tool"] for t in trace] == ["check_command", "search_docs", "check_command"]
    assert trace[0]["cost_usd"] == 0.0 and trace[1]["cost_usd"] > 0  # only searches pay for an embedding
    assert r.calls == [("docs", "x")]  # check_command never touches the retriever


def test_missing_argument_is_logged_not_run_empty() -> None:
    evidence, trace = execute_tool_calls([{"name": "CheckCommand", "args": {"query": "nc"}}],
                                         FakeRetriever(), LOOKUP, 4, 0)
    assert evidence == [] and trace[0]["error"] == "missing argument 'command'"


def test_run_tool_returns_error_instead_of_raising() -> None:
    def boom(q: str) -> list[Evidence]:
        raise TimeoutError("slow")
    results, error = run_tool(boom, "q", retries=2)
    assert results == [] and "slow" in error


def test_summarize_aggregates_trace() -> None:
    state = {"trace": [
        {"node": "classify", "model": "m", "input_tokens": 10, "output_tokens": 5, "reasoning_tokens": 0,
         "cost_usd": 0.01, "latency_s": 1.0},
        {"node": "retrieve", "model": "m", "input_tokens": 20, "output_tokens": 5, "reasoning_tokens": 1,
         "cost_usd": 0.02, "latency_s": 0.5, "tools_wall_s": 0.2},
        {"node": "retrieve", "tool": "search_docs", "cost_usd": 0.001, "latency_s": 0.2, "error": "boom"},
        {"node": "retrieve", "tool": "search_similar", "cost_usd": 0.0, "latency_s": 0.15, "error": None},
        {"node": "finalize", "cost_usd": 0.0, "latency_s": 0.0},
    ]}
    s = summarize(state, wall_s=2.0)
    assert s["usage"]["llm_calls"] == 2 and s["usage"]["input_tokens"] == 30
    assert s["usage"]["tool_calls"] == ["search_docs", "search_similar"] and s["usage"]["tool_errors"] == 1
    assert abs(s["cost_usd"] - 0.031) < 1e-9
    # LLM 0.5s + concurrent tool batch 0.2s wall (not 0.2 + 0.15 summed)
    assert abs(s["latency_s"]["retrieve"] - 0.7) < 1e-9 and s["latency_s"]["total"] == 2.0


def test_graph_wiring_v1() -> None:
    graph = build_graph().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert edges == {("__start__", "classify"), ("classify", "retrieve"), ("retrieve", "draft"),
                     ("draft", "finalize"), ("finalize", "__end__")}


def test_graph_wiring_with_grade_evidence() -> None:
    graph = build_graph(Features(grade_evidence=True)).get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert {("retrieve", "grade_evidence"), ("grade_evidence", "draft"), ("grade_evidence", "rewrite_query"),
            ("rewrite_query", "retrieve")} <= edges
    assert ("retrieve", "draft") not in edges


def test_graph_wiring_with_escalation() -> None:
    graph = build_graph(Features(grade_evidence=True, escalation_rule="confidence_not_high")).get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert {("draft", "escalate"), ("draft", "finalize"), ("escalate", "__end__")} <= edges
    with pytest.raises(ValueError):
        build_graph(Features(escalation_rule="no_such_rule"))


def _draft_state(confidence: str, sufficient: bool | None) -> dict:
    draft = AgentDraft(answer="a", commands=[], citations=[], confidence=confidence)
    state = {"draft": draft}
    if sufficient is not None:
        state["evidence_sufficient"] = sufficient
    return state


@pytest.mark.parametrize(("rule", "confidence", "sufficient", "expected"), [
    ("confidence_not_high", "high", False, "finalize"),
    ("confidence_not_high", "medium", True, "escalate"),
    ("insufficient", "high", False, "escalate"),
    ("insufficient", "low", None, "finalize"),  # grading off: no verdict, so never "insufficient"
    ("not_high_or_insufficient", "high", False, "escalate"),
    ("not_high_or_insufficient", "high", True, "finalize"),
])
def test_route_after_draft(rule: str, confidence: str, sufficient: bool | None, expected: str) -> None:
    assert route_after_draft(rule)(_draft_state(confidence, sufficient)) == expected


def test_escalate_node_keeps_draft_and_sets_status() -> None:
    out = escalate(_draft_state("low", False))
    assert out["status"] == "escalated" and "confidence=low" in out["escalation_reason"]
    assert "draft" not in out  # the draft stays in state for the human


def test_keep_relevant_filters_and_renumbers() -> None:
    evidence = renumber([ev("docs", "a"), ev("docs", "b"), ev("history", "c")])  # D1, D2, H1
    grade = EvidenceGrade(grades=[PassageGrade(id="D1", relevant=False), PassageGrade(id="D2", relevant=True),
                                  PassageGrade(id="H1", relevant=True)], sufficient=True, missing="")
    kept = keep_relevant(evidence, grade)
    assert [(e.id, e.url) for e in kept] == [("D1", "b"), ("H1", "c")]
    # passages the grader did not mention are dropped
    assert keep_relevant(evidence, EvidenceGrade(grades=[], sufficient=False, missing="x")) == []


@pytest.mark.parametrize(("sufficient", "attempts", "expected"), [
    (True, 1, "draft"), (False, 1, "rewrite_query"), (False, 2, "draft"), (True, 2, "draft"),
])
def test_next_step(sufficient: bool, attempts: int, expected: str) -> None:
    assert next_step(sufficient, attempts, max_retrievals=2) == expected


def test_retrieve_runs_planned_calls_without_llm_and_merges_kept_evidence() -> None:
    kept = renumber([ev("docs", "old")])
    state = {"ticket": "t", "evidence": kept, "retrieval_attempts": 1,
             "planned_calls": [{"name": "SearchSimilar", "args": {"query": "new"}},
                               {"name": "CheckCommand", "args": {"command": "ls"}},
                               {"name": "Bogus", "args": {"query": "z"}}]}
    runtime = SimpleNamespace(context=AgentContext(retriever=FakeRetriever(), command_lookup=LOOKUP))
    out = retrieve(state, runtime)  # planned calls run directly: no planning LLM call
    assert [e.id for e in out["evidence"]] == ["D1", "H1", "P1"]
    assert [e.url for e in out["evidence"]][:2] == ["old", "h/new"]
    assert out["retrieval_attempts"] == 2 and out["planned_calls"] == []
    assert out["queries_tried"] == ["search_similar: new", "check_command: ls"]  # unknown tool left out
    assert not any("model" in t for t in out["trace"])

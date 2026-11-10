"""Unit tests for agent pieces that need no API calls: tool execution, evidence
numbering, trace aggregation, and graph wiring."""

import json
from types import SimpleNamespace

import pytest

from agent.graph import Features, build_graph, route_after_draft
from agent.nodes.escalate import escalate
from agent.nodes.finalize import UNKNOWN_NOTE, WARNING, finalize
from agent.nodes.grade_evidence import keep_relevant, next_step
from agent.nodes.retrieve import execute_tool_calls, renumber, retrieve, run_tool
from agent.schemas import (
    AgentDraft,
    Classification,
    Evidence,
    EvidenceGrade,
    PassageGrade,
)
from agent.state import AgentContext
from agent.tools.packages import CommandPackageLookup
from agent.tools.validate import Issue
from agent.trace import summarize
from eval.run_agent import to_record


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


LOOKUP = CommandPackageLookup(
    {"noble": {"ls": ["coreutils"], "nc.openbsd": ["netcat-openbsd"]}}
)


def call(name: str, query: str) -> dict:
    return {"name": name, "args": {"query": query}, "id": "c"}


def check(command: str) -> dict:
    return {"name": "CheckCommand", "args": {"command": command}, "id": "c"}


def test_renumber_dedupes_and_numbers_per_source() -> None:
    out = renumber(
        [
            ev("docs", "a"),
            ev("package", "p"),
            ev("history", "b"),
            ev("docs", "a"),
            ev("docs", "c"),
            ev("package", "q"),
        ]
    )
    assert [(e.id, e.url) for e in out] == [
        ("D1", "a"),
        ("P1", "p"),
        ("H1", "b"),
        ("D2", "c"),
        ("P2", "q"),
    ]


def test_execute_tool_calls_runs_requested_tools() -> None:
    r = FakeRetriever()
    evidence, trace = execute_tool_calls(
        [call("SearchDocs", "rsync"), call("SearchSimilar", "copy")], r, LOOKUP, 4, 0
    )
    assert [e.id for e in evidence] == ["D1", "H1"]
    assert [t["tool"] for t in trace] == ["search_docs", "search_similar"]
    assert all(t["error"] is None and t["results"] == 1 for t in trace)


def test_no_tool_calls_means_no_evidence() -> None:
    evidence, trace = execute_tool_calls([], FakeRetriever(), LOOKUP, 4, 0)
    assert evidence == [] and trace == []


def test_tool_errors_are_retried_then_logged() -> None:
    r = FakeRetriever(fail_times=1)
    evidence, trace = execute_tool_calls(
        [call("SearchDocs", "x")], r, LOOKUP, 4, retries=1
    )
    assert len(evidence) == 1 and trace[0]["error"] is None and len(r.calls) == 2

    r = FakeRetriever(fail_times=5)
    evidence, trace = execute_tool_calls(
        [call("SearchDocs", "x")], r, LOOKUP, 4, retries=1
    )
    assert evidence == [] and "qdrant down" in trace[0]["error"]


def test_tool_call_cap_and_unknown_tool_are_logged() -> None:
    calls = [call("SearchDocs", str(i)) for i in range(3)] + [call("Bogus", "y")]
    evidence, trace = execute_tool_calls(
        calls, FakeRetriever(), LOOKUP, max_calls=2, retries=0
    )
    assert len(evidence) == 2
    assert (
        "skipped" in trace[2]["error"] and "skipped" in trace[3]["error"]
    )  # cap is checked before the name
    _, trace = execute_tool_calls([call("Bogus", "y")], FakeRetriever(), LOOKUP, 4, 0)
    assert (
        "unknown tool" in trace[0]["error"] and trace[0]["tool"] == "Bogus"
    )  # logged under the model's name


def test_check_command_tool() -> None:
    r = FakeRetriever()
    evidence, trace = execute_tool_calls(
        [check("nc"), call("SearchDocs", "x"), check("awk")], r, LOOKUP, 4, 0
    )
    assert [e.id for e in evidence] == ["P1", "D1", "P2"]
    assert "netcat-openbsd" in evidence[0].text and "not found" in evidence[2].text
    assert [t["tool"] for t in trace] == [
        "check_command",
        "search_docs",
        "check_command",
    ]
    assert (
        trace[0]["cost_usd"] == 0.0 and trace[1]["cost_usd"] > 0
    )  # only searches pay for an embedding
    assert r.calls == [("docs", "x")]  # check_command never touches the retriever


def test_missing_argument_is_logged_not_run_empty() -> None:
    evidence, trace = execute_tool_calls(
        [{"name": "CheckCommand", "args": {"query": "nc"}}],
        FakeRetriever(),
        LOOKUP,
        4,
        0,
    )
    assert evidence == [] and trace[0]["error"] == "missing argument 'command'"


def test_run_tool_returns_error_instead_of_raising() -> None:
    def boom(q: str) -> list[Evidence]:
        raise TimeoutError("slow")

    results, error = run_tool(boom, "q", retries=2)
    assert results == [] and "slow" in error


def test_summarize_aggregates_trace() -> None:
    state = {
        "trace": [
            {
                "node": "classify",
                "model": "m",
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 0,
                "cost_usd": 0.01,
                "latency_s": 1.0,
            },
            {
                "node": "retrieve",
                "model": "m",
                "input_tokens": 20,
                "output_tokens": 5,
                "reasoning_tokens": 1,
                "cost_usd": 0.02,
                "latency_s": 0.5,
                "tools_wall_s": 0.2,
            },
            {
                "node": "retrieve",
                "tool": "search_docs",
                "cost_usd": 0.001,
                "latency_s": 0.2,
                "error": "boom",
            },
            {
                "node": "retrieve",
                "tool": "search_similar",
                "cost_usd": 0.0,
                "latency_s": 0.15,
                "error": None,
            },
            {"node": "finalize", "cost_usd": 0.0, "latency_s": 0.0},
        ]
    }
    s = summarize(state, wall_s=2.0)
    assert s["usage"]["llm_calls"] == 2 and s["usage"]["input_tokens"] == 30
    assert (
        s["usage"]["tool_calls"] == ["search_docs", "search_similar"]
        and s["usage"]["tool_errors"] == 1
    )
    assert abs(s["cost_usd"] - 0.031) < 1e-9
    # LLM 0.5s + concurrent tool batch 0.2s wall (not 0.2 + 0.15 summed)
    assert (
        abs(s["latency_s"]["retrieve"] - 0.7) < 1e-9 and s["latency_s"]["total"] == 2.0
    )


def test_graph_wiring_v1() -> None:
    graph = build_graph().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert edges == {
        ("__start__", "classify"),
        ("classify", "retrieve"),
        ("retrieve", "draft"),
        ("draft", "finalize"),
        ("finalize", "__end__"),
    }


def test_graph_wiring_with_grade_evidence() -> None:
    graph = build_graph(Features(grade_evidence=True)).get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert {
        ("retrieve", "grade_evidence"),
        ("grade_evidence", "draft"),
        ("grade_evidence", "rewrite_query"),
        ("rewrite_query", "retrieve"),
    } <= edges
    assert ("retrieve", "draft") not in edges


def test_graph_wiring_with_escalation() -> None:
    graph = build_graph(
        Features(grade_evidence=True, escalation_rule="confidence_not_high")
    ).get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert {
        ("draft", "escalate"),
        ("draft", "finalize"),
        ("escalate", "__end__"),
    } <= edges
    with pytest.raises(ValueError):
        build_graph(Features(escalation_rule="no_such_rule"))


def _draft_state(confidence: str, sufficient: bool | None) -> dict:
    draft = AgentDraft(answer="a", commands=[], citations=[], confidence=confidence)
    state = {"draft": draft}
    if sufficient is not None:
        state["evidence_sufficient"] = sufficient
    return state


@pytest.mark.parametrize(
    ("rule", "confidence", "sufficient", "expected"),
    [
        ("confidence_not_high", "high", False, "finalize"),
        ("confidence_not_high", "medium", True, "escalate"),
        ("insufficient", "high", False, "escalate"),
        (
            "insufficient",
            "low",
            None,
            "finalize",
        ),  # grading off: no verdict, so never "insufficient"
        ("not_high_or_insufficient", "high", False, "escalate"),
        ("not_high_or_insufficient", "high", True, "finalize"),
    ],
)
def test_route_after_draft(
    rule: str, confidence: str, sufficient: bool | None, expected: str
) -> None:
    assert route_after_draft(rule)(_draft_state(confidence, sufficient)) == expected


def test_escalate_node_keeps_draft_and_sets_status() -> None:
    out = escalate(_draft_state("low", False))
    assert out["status"] == "escalated" and "confidence=low" in out["escalation_reason"]
    assert out["escalation_kind"] == "not_confident"
    assert "draft" not in out  # the draft stays in state for the human


def test_keep_relevant_filters_and_renumbers() -> None:
    evidence = renumber(
        [ev("docs", "a"), ev("docs", "b"), ev("history", "c")]
    )  # D1, D2, H1
    grade = EvidenceGrade(
        grades=[
            PassageGrade(id="D1", relevant=False),
            PassageGrade(id="D2", relevant=True),
            PassageGrade(id="H1", relevant=True),
        ],
        sufficient=True,
        missing="",
    )
    kept = keep_relevant(evidence, grade)
    assert [(e.id, e.url) for e in kept] == [("D1", "b"), ("H1", "c")]
    # passages the grader did not mention are dropped
    assert (
        keep_relevant(evidence, EvidenceGrade(grades=[], sufficient=False, missing="x"))
        == []
    )


@pytest.mark.parametrize(
    ("sufficient", "attempts", "expected"),
    [
        (True, 1, "draft"),
        (False, 1, "rewrite_query"),
        (False, 2, "draft"),
        (True, 2, "draft"),
    ],
)
def test_next_step(sufficient: bool, attempts: int, expected: str) -> None:
    assert next_step(sufficient, attempts, max_retrievals=2) == expected


def test_retrieve_runs_planned_calls_without_llm_and_merges_kept_evidence() -> None:
    kept = renumber([ev("docs", "old")])
    state = {
        "ticket": "t",
        "evidence": kept,
        "retrieval_attempts": 1,
        "planned_calls": [
            {"name": "SearchSimilar", "args": {"query": "new"}},
            {"name": "CheckCommand", "args": {"command": "ls"}},
            {"name": "Bogus", "args": {"query": "z"}},
        ],
    }
    runtime = SimpleNamespace(
        context=AgentContext(retriever=FakeRetriever(), command_lookup=LOOKUP)
    )
    out = retrieve(state, runtime)  # planned calls run directly: no planning LLM call
    assert [e.id for e in out["evidence"]] == ["D1", "H1", "P1"]
    assert [e.url for e in out["evidence"]][:2] == ["old", "h/new"]
    assert out["retrieval_attempts"] == 2 and out["planned_calls"] == []
    assert out["queries_tried"] == [
        "search_similar: new",
        "check_command: ls",
    ]  # unknown tool left out
    assert not any("model" in t for t in out["trace"])


def _issues(*kinds: str) -> list:
    from agent.tools.validate import Issue

    return [Issue("cmd", kind, "msg") for kind in kinds]


@pytest.mark.parametrize(
    ("kinds", "error", "attempts", "expected"),
    [
        ((), None, 0, "finalize"),
        ((), "TimeoutExpired()", 0, "escalate"),  # unchecked is never shown as clean
        (("catastrophic",), None, 0, "revise"),
        (("catastrophic",), None, 1, "revise"),
        (("catastrophic",), None, 2, "escalate"),  # out of revisions
        (("syntax",), None, 2, "escalate"),
        (
            ("unknown_command",),
            None,
            0,
            "finalize",
        ),  # warned in finalize, never revised
        (("destructive",), None, 0, "finalize"),  # warned in finalize, never revised
        (("destructive", "catastrophic"), None, 0, "revise"),  # the worst issue decides
        (("unknown_command", "syntax"), None, 1, "revise"),
    ],
)
def test_decision_after_validate(
    kinds: tuple[str, ...], error: str | None, attempts: int, expected: str
) -> None:
    from agent.nodes.validate import decision_after_validate

    assert decision_after_validate(_issues(*kinds), error, attempts, 2) == expected


def _validate_state(commands: list[str], ticket: str = "t") -> dict:
    return {
        "ticket": ticket,
        "draft": AgentDraft(
            answer="a", commands=commands, citations=[], confidence="high"
        ),
    }


def test_validate_node_records_issues_and_trace() -> None:
    from agent.nodes.validate import validate

    lookup = CommandPackageLookup({"noble": {"ls": ["coreutils"], "rm": ["coreutils"]}})
    runtime = SimpleNamespace(
        context=AgentContext(retriever=FakeRetriever(), command_lookup=lookup)
    )
    out = validate(_validate_state(["ls -la", "rm -rf ./build", "frobnicate"]), runtime)
    assert [i.kind for i in out["validation"]] == ["destructive", "unknown_command"]
    assert out["validation_error"] is None
    [t] = out["trace"]
    assert (t["node"], t["commands"], t["cost_usd"], t["error"]) == (
        "validate",
        3,
        0.0,
        None,
    )
    assert (
        t["issue_counts"] == {"destructive": 1, "unknown_command": 1}
        and type(t["issue_counts"]) is dict
    )


def test_validate_node_with_no_commands() -> None:
    from agent.nodes.validate import validate

    runtime = SimpleNamespace(
        context=AgentContext(retriever=FakeRetriever(), command_lookup=LOOKUP)
    )
    out = validate(_validate_state([]), runtime)
    assert (
        out["validation"] == []
        and out["validation_error"] is None
        and out["trace"][0]["commands"] == 0
    )


def test_validate_node_failure_is_recorded_not_reported_as_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    import agent.nodes.validate as node

    def hang(*args: object, **kwargs: object) -> list:
        raise subprocess.TimeoutExpired(cmd="bash -n", timeout=5)

    monkeypatch.setattr(node, "validate_commands", hang)
    runtime = SimpleNamespace(
        context=AgentContext(retriever=FakeRetriever(), command_lookup=LOOKUP)
    )
    out = node.validate(_validate_state(["ls"]), runtime)
    assert out["validation"] == []
    assert (
        "TimeoutExpired" in out["validation_error"]
    )  # routing can tell "unchecked" from "clean"
    assert "TimeoutExpired" in out["trace"][0]["error"]


def test_revise_node_replaces_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.nodes.revise as node
    from agent.llm import LLMResult
    from agent.tools.validate import Issue

    new = AgentDraft(
        answer="fixed",
        commands=['rm -rf "${DIR:?}"/'],
        citations=["D1"],
        confidence="medium",
    )
    sent = []

    def fake_llm(model: str, schema: type, messages: list) -> LLMResult:
        sent.append(messages[1].content)
        return LLMResult(
            model=model,
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
            cost_usd=0.001,
            latency_s=0.1,
            parsed=new,
        )

    monkeypatch.setattr(node, "invoke_structured", fake_llm)
    old = AgentDraft(
        answer="old",
        commands=[
            "cd /tmp",
            "rm -rf $DIR/",
            "dd if=x of=/dev/sda",
            r"find . -name \*.log",
        ],
        citations=["D1"],
        confidence="high",
    )
    state = {
        "ticket": "clean my build dir",
        "draft": old,
        "revise_attempts": 1,
        "evidence": [
            Evidence(
                id="D1",
                source="docs",
                title="rm(1)",
                url="u",
                text="rm removes",
                score=0.5,
            )
        ],
        "validation": [
            Issue("rm -rf $DIR/", "catastrophic", "rm -rf /: recursive delete of /"),
            Issue("dd if=x of=/dev/sda", "destructive", "dd: overwrites a disk"),
            Issue("cd /tmp", "unknown_command", "cd: never reaches the reviser"),
        ],
    }
    out = node.revise(state)

    assert out["draft"] is new and out["revise_attempts"] == 2
    [t] = out["trace"]
    assert t["node"] == "revise" and t["cost_usd"] == 0.001
    [user] = sent
    assert "clean my build dir" in user and "[D1] rm(1)" in user  # ticket and passages
    assert "cd /tmp" in user  # every old command, not only the bad ones
    assert (
        r"find . -name \*.log" in user and r"\\*" not in user
    )  # exact text, not a Python list repr
    assert "recursive delete of /" in user
    assert "overwrites a disk" not in user  # destructive is warned, not revised
    assert "never reaches the reviser" not in user  # so is unknown_command


@pytest.mark.parametrize(
    ("features", "version"),
    [
        (Features(), "agent_v1_minimal"),
        (Features(grade_evidence=True), "agent_v2_grade"),
        (Features(validate_revise=True), "agent_v4_validate_nograde"),
        (Features(grade_evidence=True, validate_revise=True), "agent_v4_validate"),
        (
            Features(
                grade_evidence=True,
                validate_revise=True,
                escalation_rule="confidence_low",
            ),
            "agent_v4_validate_confidence_low",
        ),
    ],
)
def test_version_names_differ_by_feature(features: Features, version: str) -> None:
    assert features.version == version


def test_graph_with_validate_needs_no_escalation_rule() -> None:
    graph = build_graph(Features(validate_revise=True)).get_graph()
    assert {"validate", "revise", "escalate"} <= set(graph.nodes)
    assert ("revise", "validate") in {(e.source, e.target) for e in graph.edges}
    assert ("escalate", "__end__") in {(e.source, e.target) for e in graph.edges}


def test_graph_without_validate_is_unchanged() -> None:
    assert not {"validate", "revise", "escalate"} & set(
        build_graph(Features(grade_evidence=True)).get_graph().nodes
    )


def _usage(**extra: object) -> dict:
    return {
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 1,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "latency_s": 0.0,
        **extra,
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    features: Features,
    *commands: list[str],
    confidence: str = "high",
) -> dict:
    """Run the whole graph on a fake LLM: `commands` are the successive drafts (draft,
    then each revise)."""
    import agent.nodes.classify
    import agent.nodes.draft
    import agent.nodes.retrieve
    import agent.nodes.revise
    import agent.nodes.rewrite_query
    from agent.graph import run_ticket
    from agent.llm import LLMResult, ToolCallResult

    drafts = iter(
        AgentDraft(answer="a", commands=c, citations=[], confidence=confidence)
        for c in commands
    )
    write = lambda model, schema, messages: LLMResult(**_usage(), parsed=next(drafts))  # noqa: E731
    classification = Classification(
        issue_type="other", risk_level="low", missing_info=[]
    )
    monkeypatch.setattr(
        agent.nodes.classify,
        "invoke_structured",
        lambda model, schema, messages: LLMResult(**_usage(), parsed=classification),
    )
    monkeypatch.setattr(agent.nodes.draft, "invoke_structured", write)
    monkeypatch.setattr(agent.nodes.revise, "invoke_structured", write)
    no_calls = lambda model, tools, messages: ToolCallResult(**_usage())  # noqa: E731
    monkeypatch.setattr(agent.nodes.retrieve, "invoke_tools", no_calls)
    monkeypatch.setattr(agent.nodes.rewrite_query, "invoke_tools", no_calls)
    lookup = CommandPackageLookup(
        {"noble": {"ls": ["coreutils"], "rm": ["coreutils"], "dd": ["coreutils"]}}
    )
    return run_ticket(
        build_graph(features),
        "t",
        AgentContext(retriever=FakeRetriever(), command_lookup=lookup),
    )


def _nodes(state: dict) -> list[str]:
    return [
        t["node"]
        for t in state["trace"]
        if t["node"] in ("validate", "revise", "escalate", "finalize")
    ]


VALIDATE = Features(validate_revise=True)


def test_graph_clean_draft_is_finalized(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run(monkeypatch, VALIDATE, ["ls"])
    assert out["status"] == "answered" and _nodes(out) == ["validate", "finalize"]


def test_graph_revises_then_finalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run(monkeypatch, VALIDATE, ["rm -rf /"], ["ls"])
    assert (
        out["status"] == "answered"
        and out["draft"].commands == ["ls"]
        and out["revise_attempts"] == 1
    )
    assert _nodes(out) == ["validate", "revise", "validate", "finalize"]


def test_graph_escalates_when_revisions_run_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _run(monkeypatch, VALIDATE, ["rm -rf /"], ["rm -rf /"], ["rm -rf /"])
    assert out["status"] == "escalated" and out["revise_attempts"] == 2
    assert _nodes(out) == [
        "validate",
        "revise",
        "validate",
        "revise",
        "validate",
        "escalate",
    ]
    reason = out["escalation_reason"]
    assert out["escalation_kind"] == "unsafe"
    assert "after 2 revisions" in reason and "`rm -rf /` (catastrophic)" in reason
    assert "confidence" not in reason  # not the rule's reason


def test_graph_unknown_command_is_warned_not_revised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _run(monkeypatch, VALIDATE, ["frobnicate"])
    assert out["status"] == "answered" and _nodes(out) == ["validate", "finalize"]
    assert (
        out["final_answer"].startswith(UNKNOWN_NOTE)
        and "frobnicate" in out["final_answer"]
    )


def test_graph_unknown_command_does_not_stop_other_fixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _run(monkeypatch, VALIDATE, ["frobnicate", "rm -rf /"], ["frobnicate", "ls"])
    assert out["status"] == "answered" and out["revise_attempts"] == 1
    assert [i.kind for i in out["revisions"][0]["issues"]] == [
        "catastrophic"
    ]  # unknown_command not sent
    assert out["final_answer"].startswith(UNKNOWN_NOTE)


def test_graph_destructive_is_not_revised(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run(monkeypatch, VALIDATE, ["rm -rf ./build"])
    assert out["status"] == "answered" and _nodes(out) == ["validate", "finalize"]


def test_graph_failed_check_escalates_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.nodes.validate as node

    def hang(*args: object) -> list:
        raise TimeoutError("bash -n")

    monkeypatch.setattr(node, "validate_commands", hang)
    out = _run(monkeypatch, VALIDATE, ["ls"])
    assert out["status"] == "escalated" and _nodes(out) == ["validate", "escalate"]
    assert (
        "could not run" in out["escalation_reason"]
        and "TimeoutError" in out["escalation_reason"]
    )
    assert out["escalation_kind"] == "unchecked"


def test_graph_rule_is_checked_before_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    features = Features(validate_revise=True, escalation_rule="confidence_low")
    out = _run(monkeypatch, features, ["rm -rf /"], confidence="low")
    assert out["status"] == "escalated" and _nodes(out) == ["escalate"]
    assert (
        "confidence=low" in out["escalation_reason"]
        and out["escalation_kind"] == "not_confident"
    )


def test_graph_longest_path_fits_max_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    features = Features(
        grade_evidence=True, validate_revise=True
    )  # no evidence: uses every retrieval round
    out = _run(monkeypatch, features, ["rm -rf /"], ["rm -rf /"], ["rm -rf /"])
    assert (
        out["status"] == "escalated"
        and out["retrieval_attempts"] == 2
        and out["revise_attempts"] == 2
    )


def test_record_saves_validation_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _run(monkeypatch, VALIDATE, ["rm -rf /"], ["rm -rf ./build"])
    record = to_record({"question_id": 1, "split": "dev"}, state, wall_s=1.0)
    assert (
        json.loads(json.dumps(record))["revise_attempts"] == 1
    )  # Issue objects would not serialize
    assert (
        record["decision_after_validate"] == "finalize"
        and record["validation_error"] is None
    )
    assert [i["kind"] for i in record["validation"]] == [
        "destructive"
    ]  # issues of the last check, as dicts
    [revision] = record["revisions"]  # what the revision replaced, and why
    assert (
        revision["draft"]["commands"] == ["rm -rf /"]
        and revision["issues"][0]["kind"] == "catastrophic"
    )


def test_record_without_validate_has_empty_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = to_record(
        {"question_id": 1, "split": "dev"},
        _run(monkeypatch, Features(), ["ls"]),
        wall_s=1.0,
    )
    assert (
        record["validation"] == []
        and record["revise_attempts"] == 0
        and record["decision_after_validate"] is None
    )


def _finalize(*kinds: str, validation: bool = True) -> tuple[dict, AgentDraft]:
    draft = AgentDraft(
        answer="Run it.", commands=["c"], citations=["D1"], confidence="high"
    )
    state = {
        "draft": draft,
        "validation": [
            Issue(f"cmd{i}", kind, f"msg{i}") for i, kind in enumerate(kinds)
        ],
    }
    if not validation:
        del state["validation"]
    return finalize(state), draft


@pytest.mark.parametrize(
    ("kinds", "validation"),
    [
        ((), False),  # validate is off
        ((), True),
        (
            ("syntax",),
            True,
        ),  # cannot reach finalize; no heading for it, so nothing is shown
    ],
)
def test_finalize_without_warnings_shows_the_draft(
    kinds: tuple[str, ...], validation: bool
) -> None:
    out, draft = _finalize(*kinds, validation=validation)
    assert out["status"] == "answered" and out["final_answer"] == "Run it."


def test_finalize_lists_risky_commands_first() -> None:
    out, _ = _finalize("unknown_command", "destructive", "destructive")
    assert out["final_answer"] == (
        f"{WARNING}\n- `cmd1` (destructive): msg1\n- `cmd2` (destructive): msg2\n\n"
        f"{UNKNOWN_NOTE}\n- `cmd0` (unknown_command): msg0\n\nRun it."
    )


def test_finalize_leaves_the_draft_for_the_judge() -> None:
    out, draft = _finalize("destructive")
    assert "draft" not in out and draft.answer == "Run it."


def test_graph_warning_reaches_the_asker_but_not_the_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _run(monkeypatch, VALIDATE, ["rm -rf ./build"])
    record = to_record({"question_id": 1, "split": "dev"}, state, wall_s=1.0)
    assert (
        record["final_answer"].startswith(WARNING)
        and "rm -rf ./build" in record["final_answer"]
    )
    assert record["draft"]["answer"] == "a"


def test_escalated_ticket_has_no_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _run(monkeypatch, VALIDATE, ["rm -rf /"], ["rm -rf /"], ["rm -rf /"])
    assert (
        to_record({"question_id": 1, "split": "dev"}, state, wall_s=1.0)["final_answer"]
        is None
    )


def _history_hit(question_author: str, answer_author: str) -> object:
    from llama_index.core.schema import NodeWithScore, TextNode

    metadata = {
        "title": "t",
        "url": "https://unix.stackexchange.com/q/1",
        "answer_text": "use ls",
        "question_author": question_author,
        "answer_author": answer_author,
        "license": "CC BY-SA 3.0",
    }
    return NodeWithScore(node=TextNode(text="q", metadata=metadata), score=0.7)


def test_history_passage_credits_both_authors_once() -> None:
    from agent.tools.retrieval import _history_evidence

    e = _history_evidence(_history_hit("asker", "answerer"), 1)
    assert (e.authors, e.license) == (["asker", "answerer"], "CC BY-SA 3.0")
    assert "by answerer, CC BY-SA 3.0" in e.text  # the LLM still sees the credit
    assert _history_evidence(_history_hit("same", "same"), 1).authors == [
        "same"
    ]  # self-answered question

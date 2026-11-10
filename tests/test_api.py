"""Tests for the HTTP API with a fake graph: no OpenAI, Qdrant, or package table
needed."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main
from agent.schemas import AgentDraft, Evidence
from api.budget import DailyBudget
from api.handoff import save_handoff
from api.main import FEATURES, PUBLIC_REASON, app
from eval.common import load_jsonl


class FakeGraph:
    """Stands in for the compiled graph: returns a fixed final state and records the
    input."""

    def __init__(self, state: dict) -> None:
        self.state = state
        self.inputs: list[dict] = []

    def invoke(self, state: dict, **kwargs: object) -> dict:
        self.inputs.append(state)
        return self.state


def _evidence(id_: str) -> Evidence:
    return Evidence(
        id=id_,
        source="docs",
        title=f"title {id_}",
        url=f"https://x/{id_}",
        text="secret text",
        score=0.5,
    )


DRAFT = AgentDraft(
    answer="Use ls [D2].", commands=["ls"], citations=["D2"], confidence="high"
)
TRACE = [
    {
        "node": "draft",
        "model": "m",
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 0,
        "cost_usd": 0.0012,
        "latency_s": 1.0,
    }
]
ANSWERED = {
    "status": "answered",
    "draft": DRAFT,
    "final_answer": "WARNING...\n\nUse ls [D2].",
    "evidence": [_evidence("D1"), _evidence("D2"), _evidence("H1")],
    "trace": TRACE,
}


@pytest.fixture
def queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the hand-off queue to a temporary file, so tests never write into the
    real one. Patching HANDOFF_PATH would not work: save_handoff's default was fixed
    when it was defined."""
    path = tmp_path / "handoff.jsonl"
    monkeypatch.setattr(
        api.main, "save_handoff", lambda record: save_handoff(record, path)
    )
    return path


@pytest.fixture
def client(queue: Path) -> TestClient:
    # no `with`: the lifespan does not run, so the real graph is never built
    api.main.limiter.reset()  # every test request comes from the same address
    app.state.daily_budget = DailyBudget(
        limit_usd=1.0
    )  # set by the lifespan, which tests skip
    return TestClient(app)


def _post(client: TestClient, state: dict) -> tuple[dict, FakeGraph]:
    graph = FakeGraph(state)
    app.state.graph, app.state.context = graph, object()
    response = client.post(
        "/tickets", json={"title": "ls hangs", "question_text": "Why?"}
    )
    assert response.status_code == 200, response.text
    return response.json(), graph


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "version": FEATURES.version}


def test_answered_ticket_returns_final_answer(client: TestClient) -> None:
    body, graph = _post(client, ANSWERED)
    assert (
        body["status"] == "answered" and body["answer"] == ANSWERED["final_answer"]
    )  # warnings included
    assert body["escalation_reason"] is None and len(body["ticket_id"]) == 32
    assert graph.inputs[0]["ticket"] == "ls hangs\n\nWhy?"  # same format as eval


def test_answered_ticket_cites_only_what_the_answer_cites(client: TestClient) -> None:
    body, _ = _post(client, ANSWERED)
    assert body["citations"] == [
        {
            "id": "D2",
            "title": "title D2",
            "url": "https://x/D2",
            "authors": [],
            "license": None,
        }
    ]


def test_inline_citation_missing_from_the_list_still_counts(client: TestClient) -> None:
    draft = DRAFT.model_copy(
        update={"answer": "Use ls [D2], see also (H1).", "citations": ["D2"]}
    )
    body, _ = _post(client, ANSWERED | {"draft": draft})
    assert [c["id"] for c in body["citations"]] == ["D2", "H1"]
    assert "secret text" not in str(body)  # passage text stays internal


@pytest.mark.parametrize("kind", ["unchecked", "unsafe", "not_confident"])
def test_escalated_ticket_shows_only_the_public_reason(
    client: TestClient, kind: str
) -> None:
    body, _ = _post(client, _escalated(kind))
    assert (
        body["status"] == "escalated"
        and body["escalation_reason"] == PUBLIC_REASON[kind]
    )
    assert body["answer"] is None and body["citations"] == []
    assert "TimeoutExpired" not in str(body)


def _escalated(kind: str) -> dict:
    return ANSWERED | {
        "status": "escalated",
        "escalation_kind": kind,
        "escalation_reason": "validation could not run: TimeoutExpired(cmd=['bash', "
        "'-n'])",
        "validation_error": "TimeoutExpired(cmd=['bash', '-n'], timeout=5.0)",
    }


def test_escalated_ticket_is_queued_for_a_human(
    client: TestClient, queue: Path
) -> None:
    body, _ = _post(client, _escalated("unchecked"))
    [record] = load_jsonl(queue)
    assert record["ticket_id"] == body["ticket_id"]  # links the two sides
    assert (
        record["ticket"] == "ls hangs\n\nWhy?" and record["version"] == FEATURES.version
    )
    assert record["escalation_reason"] == "validation could not run: TimeoutExpired"


def test_answered_ticket_is_not_queued(client: TestClient, queue: Path) -> None:
    _post(client, ANSWERED)
    assert not queue.exists()


def test_ticket_ids_are_unique(client: TestClient) -> None:
    assert (
        _post(client, ANSWERED)[0]["ticket_id"]
        != _post(client, ANSWERED)[0]["ticket_id"]
    )


def test_missing_field_is_rejected_before_the_agent_runs(client: TestClient) -> None:
    graph = FakeGraph(ANSWERED)
    app.state.graph, app.state.context = graph, object()
    assert client.post("/tickets", json={"title": "only a title"}).status_code == 422
    assert graph.inputs == []  # no LLM spend on bad input


def _post_raw(client: TestClient, title: str, question: str) -> tuple:
    graph = FakeGraph(ANSWERED)
    app.state.graph, app.state.context = graph, object()
    return client.post(
        "/tickets", json={"title": title, "question_text": question}
    ), graph


def test_question_at_the_tested_limit_runs_the_agent(
    client: TestClient, queue: Path
) -> None:
    response, graph = _post_raw(client, "t", "x" * api.main.MAX_QUESTION_CHARS)
    assert response.json()["status"] == "answered" and len(graph.inputs) == 1


def test_longer_question_is_escalated_without_running_the_agent(
    client: TestClient, queue: Path
) -> None:
    response, graph = _post_raw(client, "t", "x" * (api.main.MAX_QUESTION_CHARS + 1))
    body = response.json()
    assert response.status_code == 200 and body["status"] == "escalated"
    assert (
        body["escalation_reason"] == PUBLIC_REASON["too_long"] and graph.inputs == []
    )  # no LLM spend
    [record] = load_jsonl(queue)
    assert (
        record["ticket_id"] == body["ticket_id"]
        and record["escalation_kind"] == "too_long"
    )
    assert record["draft"] is None and "4001" in record["escalation_reason"]


@pytest.mark.parametrize(("title", "question"), [("t", "x" * 30_001), ("t" * 201, "q")])
def test_absurd_sizes_are_rejected(
    client: TestClient, queue: Path, title: str, question: str
) -> None:
    response, graph = _post_raw(client, title, question)
    assert response.status_code == 422 and graph.inputs == [] and not queue.exists()


def test_log_has_outcome_and_cost_but_not_the_ticket(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="api.main")
    body, _ = _post(client, ANSWERED)
    [line] = [r.getMessage() for r in caplog.records if r.name == "api.main"]
    assert (
        line.startswith(f"ticket {body['ticket_id']}: answered, $0.0012")
        and "1 LLM calls" in line
    )
    assert "ls hangs" not in caplog.text  # user data stays out


def test_failed_check_logs_the_full_error(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="api.main")
    _post(client, _escalated("unchecked"))
    [warning] = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "timeout=5.0" in warning.getMessage()  # the detail the queue drops


class BrokenGraph:
    def invoke(self, state: dict, **kwargs: object) -> dict:
        raise RuntimeError("openai: secret internal detail")


def test_agent_failure_is_a_generic_500(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    app.state.graph, app.state.context = BrokenGraph(), object()
    response = client.post("/tickets", json={"title": "t", "question_text": "q"})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert (
        detail.startswith("Internal error. Reference: ")
        and "secret" not in response.text
    )
    [record] = [
        r for r in caplog.records if r.levelname == "ERROR" and r.name == "api.main"
    ]
    assert (
        record.getMessage() == f"ticket {detail.split()[-1]}: failed"
    )  # same ticket_id in reply and log
    assert (
        record.exc_info and "secret internal detail" in caplog.text
    )  # full error kept for the developer


def test_queue_failure_is_a_500_not_a_fake_hand_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(record: dict) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(api.main, "save_handoff", disk_full)
    app.state.graph, app.state.context = FakeGraph(_escalated("unsafe")), object()
    assert (
        client.post("/tickets", json={"title": "t", "question_text": "q"}).status_code
        == 500
    )


def test_sixth_ticket_in_a_minute_is_refused_before_the_agent_runs(
    client: TestClient,
) -> None:
    graph = FakeGraph(ANSWERED)
    app.state.graph, app.state.context = graph, object()
    codes = [
        client.post("/tickets", json={"title": "t", "question_text": "q"}).status_code
        for _ in range(6)
    ]
    assert (
        codes == [200] * 5 + [429] and len(graph.inputs) == 5
    )  # no LLM spend on the 6th
    assert client.get("/health").status_code == 200  # health is not limited


def test_spent_budget_refuses_tickets_without_running_the_agent(
    client: TestClient,
) -> None:
    app.state.daily_budget.add(1.0)
    response, graph = _post_raw(client, "t", "q")
    assert (
        response.status_code == 503 and "budget" in response.json()["detail"]
    )  # not turned into a 500
    assert graph.inputs == []


def test_each_ticket_cost_is_added_to_the_budget(client: TestClient) -> None:
    app.state.daily_budget = DailyBudget(limit_usd=0.002)
    assert _post_raw(client, "t", "q")[0].status_code == 200  # spends $0.0012
    assert _post_raw(client, "t", "q")[0].status_code == 200  # $0.0024 after this one
    assert _post_raw(client, "t", "q")[0].status_code == 503


def test_too_long_question_needs_no_budget(client: TestClient) -> None:
    app.state.daily_budget.add(1.0)  # the agent would not run anyway
    response, _ = _post_raw(client, "t", "x" * (api.main.MAX_QUESTION_CHARS + 1))
    assert response.json()["escalation_reason"] == PUBLIC_REASON["too_long"]


def test_stack_exchange_citation_credits_authors_and_license(
    client: TestClient,
) -> None:
    past = Evidence(
        id="H1",
        source="history",
        title="past question",
        url="https://unix.stackexchange.com/q/1",
        text="secret text",
        score=0.5,
        authors=["asker", "answerer"],
        license="CC BY-SA 4.0",
    )
    draft = DRAFT.model_copy(update={"answer": "As in [H1].", "citations": ["H1"]})
    body, _ = _post(client, ANSWERED | {"draft": draft, "evidence": [past]})
    assert body["citations"] == [
        {
            "id": "H1",
            "title": "past question",
            "url": "https://unix.stackexchange.com/q/1",
            "authors": ["asker", "answerer"],
            "license": "CC BY-SA 4.0",
        }
    ]  # CC BY-SA credit

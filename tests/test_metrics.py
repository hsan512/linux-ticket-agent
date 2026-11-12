"""Tests for the Prometheus metrics: each way a ticket can end moves exactly the right
series, and GET /metrics serves them without ticket text.

Metrics live in prometheus_client's process-wide registry and are never reset, so the
tests compare values before and after a request instead of expecting absolute counts.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from api_fakes import ANSWERED, BrokenGraph, FakeGraph, escalated
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import api.main
from agent.trace import summarize
from api.main import MAX_QUESTION_CHARS, Outcome, app

OUTCOMES = ("answered", "escalated", "too_long", "budget_exhausted", "error")
TICKET = {"title": "ls hangs", "question_text": "Why?"}


def value(name: str, **labels: str) -> float:
    """Current value of one series; 0 if it does not exist yet."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def snapshot() -> dict[str, float]:
    """Every series these tests look at, keyed by a readable name."""
    snap = {f"tickets:{o}": value("tickets_total", outcome=o) for o in OUTCOMES}
    for kind in api.main.PUBLIC_REASON:
        snap[f"escalations:{kind}"] = value("escalations_total", kind=kind)
    snap["duration_count"] = value("ticket_duration_seconds_count")
    snap["cost_count"] = value("ticket_cost_usd_count")
    snap["cost_sum"] = value("ticket_cost_usd_sum")
    snap["revisions"] = value("ticket_revisions_total")
    return snap


def changes(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """Only the series that moved, with how much they moved."""
    return {k: round(after[k] - before[k], 6) for k in after if after[k] != before[k]}


def run(client: TestClient, graph: object, ticket: dict = TICKET) -> tuple[int, dict]:
    """POST one ticket with `graph` standing in for the agent; returns the status code
    and the metric changes it caused."""
    app.state.graph, app.state.context = graph, object()
    before = snapshot()
    status = client.post("/tickets", json=ticket).status_code
    return status, changes(before, snapshot())


def test_every_series_exists_before_the_first_ticket() -> None:
    for outcome in OUTCOMES:
        assert (
            REGISTRY.get_sample_value("tickets_total", {"outcome": outcome}) is not None
        )
    for kind in api.main.PUBLIC_REASON:
        assert (
            REGISTRY.get_sample_value("escalations_total", {"kind": kind}) is not None
        )


def test_outcome_list_matches_the_type() -> None:
    from typing import get_args

    assert set(get_args(Outcome)) == set(OUTCOMES)


def test_answered_ticket(client: TestClient) -> None:
    status, moved = run(client, FakeGraph(ANSWERED))
    cost = summarize(ANSWERED, 0.0)["cost_usd"]
    assert status == 200
    assert moved == {
        "tickets:answered": 1,
        "duration_count": 1,
        "cost_count": 1,
        "cost_sum": round(cost, 6),
    }  # no revisions in this run, so that counter stays put


def test_revisions_are_added_up(client: TestClient) -> None:
    _, moved = run(client, FakeGraph(ANSWERED | {"revise_attempts": 2}))
    assert moved["revisions"] == 2


@pytest.mark.parametrize("kind", ["unchecked", "unsafe", "not_confident"])
def test_escalated_ticket_counts_its_kind(client: TestClient, kind: str) -> None:
    status, moved = run(client, FakeGraph(escalated(kind)))
    assert status == 200
    assert moved["tickets:escalated"] == 1 and moved[f"escalations:{kind}"] == 1
    assert moved["duration_count"] == 1  # the agent ran
    assert [k for k in moved if k.startswith("escalations:")] == [f"escalations:{kind}"]


def test_too_long_question_is_counted_without_an_agent_run(client: TestClient) -> None:
    graph = FakeGraph(ANSWERED)
    ticket = {"title": "t", "question_text": "x" * (MAX_QUESTION_CHARS + 1)}
    status, moved = run(client, graph, ticket)
    assert status == 200 and graph.inputs == []
    assert moved == {"tickets:too_long": 1, "escalations:too_long": 1}  # no duration


def test_spent_budget_is_counted(client: TestClient) -> None:
    app.state.daily_budget.add(1.0)
    status, moved = run(client, FakeGraph(ANSWERED))
    assert status == 503
    assert moved == {"tickets:budget_exhausted": 1}


def test_agent_failure_is_counted_as_error(client: TestClient) -> None:
    status, moved = run(client, BrokenGraph())
    assert status == 500
    assert moved == {"tickets:error": 1}


def test_queue_failure_is_an_error_not_an_escalation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(record: dict) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(api.main, "save_handoff", disk_full)
    status, moved = run(client, FakeGraph(escalated("unsafe")))
    assert status == 500
    assert moved["tickets:error"] == 1
    assert "tickets:escalated" not in moved and "escalations:unsafe" not in moved


def test_rate_limited_request_is_not_a_ticket(client: TestClient) -> None:
    app.state.graph, app.state.context = FakeGraph(ANSWERED), object()
    for _ in range(5):
        client.post("/tickets", json=TICKET)
    before = snapshot()
    assert client.post("/tickets", json=TICKET).status_code == 429
    assert changes(before, snapshot()) == {}


def test_metrics_endpoint_serves_the_series_without_ticket_text(
    client: TestClient,
) -> None:
    secret = "my-db-password-is-hunter2"
    run(client, FakeGraph(ANSWERED), {"title": secret, "question_text": secret})
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for name in ("tickets_total", "escalations_total", "ticket_duration_seconds"):
        assert name in response.text
    assert secret not in response.text


def test_http_metrics_skip_metrics_and_health(client: TestClient) -> None:
    run(client, FakeGraph(ANSWERED))
    client.get("/health")
    client.get("/metrics")
    text = client.get("/metrics").text
    assert 'handler="/tickets"' in text
    assert 'handler="/metrics"' not in text and 'handler="/health"' not in text


@pytest.fixture
def started_app(
    queue: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[], TestClient]:
    """The app with its real lifespan, but fake heavy dependencies, so the startup
    wiring (here: the budget gauge) is tested too."""
    monkeypatch.setattr(
        api.main, "load_command_packages", lambda path: {"noble": {"ls": ["coreutils"]}}
    )
    monkeypatch.setattr(api.main, "Retriever", lambda: object())
    monkeypatch.setattr(api.main, "build_graph", lambda features: FakeGraph(ANSWERED))
    api.main.limiter.reset()
    return lambda: TestClient(app)


def test_budget_gauge_follows_spending(started_app: Callable[[], TestClient]) -> None:
    with started_app() as client:  # `with` runs the lifespan
        assert value("daily_budget_spent_usd") == 0.0
        assert client.post("/tickets", json=TICKET).status_code == 200
        cost = summarize(ANSWERED, 0.0)["cost_usd"]
        assert value("daily_budget_spent_usd") == pytest.approx(cost)
        assert "daily_budget_spent_usd" in client.get("/metrics").text

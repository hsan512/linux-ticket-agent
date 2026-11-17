"""Tests for the CloudFront front door: client_ip (the rate-limit key) and
verify_origin (only requests carrying our distribution's secret header get in)."""

import pytest
from api_fakes import ANSWERED, FakeGraph
from fastapi.testclient import TestClient
from starlette.requests import Request

import api.main
from api.main import TICKET_RATE_LIMIT, app, client_ip

SECRET = "test-origin-secret"
TICKET = {"title": "ls hangs", "question_text": "Why?"}
PER_MINUTE = int(TICKET_RATE_LIMIT.split("/")[0])


def request_with(headers: dict[str, str], client: tuple[str, int] | None) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


# --- client_ip -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("198.51.100.10:46532", "198.51.100.10"),
        ("2001:db8::1:46532", "2001:db8::1"),  # IPv6: split on the LAST colon
        ("[2001:db8::1]:46532", "2001:db8::1"),  # brackets, in case they appear
        ("198.51.100.10", "198.51.100.10"),  # no port: keep it, never ""
    ],
)
def test_client_ip_reads_the_cloudfront_header(header: str, expected: str) -> None:
    request = request_with({"CloudFront-Viewer-Address": header}, ("10.0.0.5", 1))
    assert client_ip(request) == expected


@pytest.mark.parametrize("headers", [{}, {"CloudFront-Viewer-Address": ""}])
def test_client_ip_falls_back_to_the_socket_address(headers: dict) -> None:
    assert client_ip(request_with(headers, ("10.0.0.5", 1))) == "10.0.0.5"


def test_client_ip_ignores_x_forwarded_for() -> None:
    """The leftmost X-Forwarded-For entry is written by the client; trusting it let
    anyone pick a new fake IP per request and skip the rate limit."""
    request = request_with(
        {
            "X-Forwarded-For": "6.6.6.6, 1.2.3.4",
            "CloudFront-Viewer-Address": "1.2.3.4:443",
        },
        ("10.0.0.5", 1),
    )
    assert client_ip(request) == "1.2.3.4"


def test_the_limiter_uses_client_ip() -> None:
    assert api.main.limiter._key_func is client_ip


def test_rate_limit_counts_each_viewer_separately(client: TestClient) -> None:
    """Behind CloudFront every request has CloudFront's socket address; the limit must
    still be per viewer, and a fake X-Forwarded-For must not reset it."""
    app.state.graph, app.state.context = FakeGraph(ANSWERED), object()
    viewer = "CloudFront-Viewer-Address"
    for i in range(PER_MINUTE):
        headers = {viewer: "1.2.3.4:5000", "X-Forwarded-For": f"9.9.9.{i}"}
        assert client.post("/tickets", json=TICKET, headers=headers).status_code == 200
    blocked = client.post(
        "/tickets",
        json=TICKET,
        headers={viewer: "1.2.3.4:6000", "X-Forwarded-For": "8.8.8.8"},
    )
    assert blocked.status_code == 429  # same viewer, other port and fake XFF
    other = client.post("/tickets", json=TICKET, headers={viewer: "5.6.7.8:5000"})
    assert other.status_code == 200  # a different viewer has its own counter


# --- verify_origin ---------------------------------------------------------------


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(api.main, "ORIGIN_SECRET", SECRET)
    return SECRET


def from_ip(host: str) -> TestClient:
    return TestClient(app, client=(host, 50000))


def test_no_secret_configured_lets_everything_through(client: TestClient) -> None:
    assert api.main.ORIGIN_SECRET == ""  # local dev and CI
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/metrics").status_code == 200


@pytest.mark.parametrize("path", ["/openapi.json", "/docs"])
def test_right_secret_passes(client: TestClient, secret: str, path: str) -> None:
    assert client.get(path, headers={"X-Origin-Verify": secret}).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Origin-Verify": ""},
        {"X-Origin-Verify": "wrong"},
        {"X-Origin-Verify": SECRET[:-1]},  # a prefix of the secret is not the secret
        {"X-Origin-Verify": "sécret".encode("latin-1")},  # non-ASCII: 403, not 500
    ],
)
def test_missing_or_wrong_secret_is_forbidden(
    client: TestClient, secret: str, headers: dict
) -> None:
    response = client.get("/openapi.json", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_forbidden_ticket_never_runs_the_agent(client: TestClient, secret: str) -> None:
    graph = FakeGraph(ANSWERED)
    app.state.graph, app.state.context = graph, object()
    assert client.post("/tickets", json=TICKET).status_code == 403
    assert graph.inputs == []


def test_ticket_with_the_secret_runs(client: TestClient, secret: str) -> None:
    app.state.graph, app.state.context = FakeGraph(ANSWERED), object()
    response = client.post("/tickets", json=TICKET, headers={"X-Origin-Verify": secret})
    assert response.status_code == 200


def test_health_needs_no_secret(client: TestClient, secret: str) -> None:
    assert client.get("/health").status_code == 200  # deploy.sh and Docker check it


@pytest.mark.parametrize("host", ["172.18.0.3", "10.0.0.7", "127.0.0.1"])
def test_metrics_open_to_private_addresses(client, secret: str, host: str) -> None:
    assert from_ip(host).get("/metrics").status_code == 200  # Prometheus in compose


@pytest.mark.parametrize("host", ["8.8.8.8", "testclient"])
def test_metrics_forbidden_to_public_or_unparsable(client, secret, host) -> None:
    assert from_ip(host).get("/metrics").status_code == 403


def test_metrics_with_the_secret_from_a_public_address(client, secret) -> None:
    """Allowed by verify_origin; CloudFront itself blocks /metrics at the edge."""
    response = from_ip("8.8.8.8").get("/metrics", headers={"X-Origin-Verify": secret})
    assert response.status_code == 200


def test_private_address_does_not_open_other_paths(client, secret) -> None:
    assert from_ip("172.18.0.3").get("/openapi.json").status_code == 403

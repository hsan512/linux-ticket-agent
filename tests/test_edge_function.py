"""Tests for the CloudFront Function (infra/edge.js.tftpl): rendered the way Terraform's
templatefile does it, then run in Node with fake CloudFront events. Node is not
CloudFront's runtime, but the function uses only plain JavaScript that both share.
Skipped where Node is missing (GitHub's ubuntu runners have it)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "infra" / "edge.js.tftpl"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# the allow-list: the API's public paths (and FastAPI's docs helper page)
PASS_THROUGH = [
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/tickets",
    "/health",
]
# everything else is answered at the edge and never reaches the instance
NOT_FOUND = [
    "/",
    "/metrics",
    "/metrics/",
    "/metrics/anything",
    "/metricsx",
    "/admin",
    "/.env",
    "/ticket",  # near-misses of allowed paths
    "/healthz",
    "/docsx",
]


def render(online: bool) -> str:
    """templatefile with {online = <bool>}: Terraform writes a bool as true/false."""
    source = TEMPLATE.read_text()
    assert "`" not in source, (
        "backticks: Terraform would treat ${...} in them as its own"
    )
    return source.replace("${online}", "true" if online else "false")


def run(online: bool, uri: str, method: str = "GET") -> dict:
    """Call handler(event) and return what it returned (a request or a response)."""
    event = {"request": {"uri": uri, "method": method, "headers": {}}}
    call = f"handler({json.dumps(event)}) ?? null"
    script = f"{render(online)}\nprocess.stdout.write(JSON.stringify({call}));"
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"{uri}: the function threw\n{result.stderr}"
    returned = json.loads(result.stdout)
    assert returned is not None, (
        f"{uri}: the function returned nothing (CloudFront errors)"
    )
    return returned


def is_request(returned: dict, uri: str) -> bool:
    return returned.get("uri") == uri and "statusCode" not in returned


@pytest.mark.parametrize("uri", PASS_THROUGH)
def test_online_passes_requests_through(uri: str) -> None:
    assert is_request(run(True, uri), uri)


def test_online_passes_a_post_ticket_through() -> None:
    assert is_request(run(True, "/tickets", "POST"), "/tickets")


@pytest.mark.parametrize("uri", NOT_FOUND)
def test_other_paths_get_404_at_the_edge(uri: str) -> None:
    assert run(True, uri)["statusCode"] == 404


def test_metrics_404_looks_like_any_missing_path() -> None:
    """Same answer as for a path that does not exist, so /metrics is not revealed."""
    blocked, missing = run(True, "/metrics"), run(True, "/no-such-path")
    assert blocked == missing


@pytest.mark.parametrize("uri", [*PASS_THROUGH, *NOT_FOUND])
def test_offline_answers_503_everywhere(uri: str) -> None:
    returned = run(False, uri)
    assert returned["statusCode"] == 503


@pytest.mark.parametrize("online", [True, False])
def test_generated_responses_are_well_formed(online: bool) -> None:
    """CloudFront's format: lower-case header names, each {value: ...}; never cached."""
    returned = run(online, "/metrics")
    assert returned["statusCode"] == (404 if online else 503)
    assert isinstance(returned["statusDescription"], str)
    headers = returned["headers"]
    assert all(name == name.lower() for name in headers)
    assert all(set(header) == {"value"} for header in headers.values())
    assert headers["cache-control"]["value"] == "no-store"
    assert returned["body"]["encoding"] == "text"
    assert returned["body"]["data"].endswith("\n")  # a terminal prompt starts cleanly


def test_offline_message_explains_itself() -> None:
    assert "offline" in run(False, "/docs")["body"]["data"]


def test_not_found_message_points_to_the_docs() -> None:
    assert "/docs" in run(True, "/nope")["body"]["data"]


def test_fits_cloudfront_function_size_limit() -> None:
    assert len(render(True).encode()) < 10_240  # 10 KB maximum

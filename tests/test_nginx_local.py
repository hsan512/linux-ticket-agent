"""Tests that the local nginx (deploy/nginx.local.conf, the stand-in for CloudFront)
behaves like production: the same paths reach the API, /metrics is blocked, and the
page gets the same Content-Security-Policy as its meta tag. Runs edge.js.tftpl in
Node for the comparison, so it is skipped where Node is missing."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONF = (ROOT / "deploy" / "nginx.local.conf").read_text()
PROXY_CONF = (ROOT / "deploy" / "nginx.api_proxy.conf").read_text()
EDGE = (ROOT / "infra" / "edge.js.tftpl").read_text()
HTML = (ROOT / "frontend" / "index.html").read_text()

PATHS = [
    "/",
    "/index.html",
    "/app.js",
    "/tickets",
    "/tickets/x",
    "/health",
    "/healthz",
    "/docs",
    "/docs/oauth2-redirect",
    "/docsx",
    "/openapi.json",
    "/openapi.jsonx",
    "/metrics",
    "/metrics/",
    "/metrics/x",
    "/metricsx",
    "/admin",
    "/.env",
]


def nginx_route(path: str) -> str:
    """Where the nginx config sends a path: "api", "blocked" or "page" (the same
    precedence nginx uses: exact match, then regexes in order, then the prefix /)."""
    for match in re.finditer(r"location\s+=\s+(\S+)\s*\{([^}]*)\}", CONF):
        if path == match.group(1):
            return "api" if "proxy_pass" in match.group(2) else "blocked"
    for match in re.finditer(r"location\s+~\s+(\S+)\s*\{([^}]*)\}", CONF):
        if re.search(match.group(1), path):
            return "api" if "proxy_pass" in match.group(2) else "blocked"
    return "page"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_api_paths_match_the_edge_function() -> None:
    """In production the edge function decides which paths reach the API; every
    other path goes to S3 (the page). nginx must route the same way."""
    script = EDGE.replace("${online}", "true") + (
        "\nconst out = {};"
        f"\nfor (const uri of {json.dumps(PATHS)}) {{"
        "\n  const r = handler({request: {uri, method: 'GET', headers: {}}});"
        "\n  out[uri] = r.statusCode ? 'not-api' : 'api';"
        "\n}"
        "\nprocess.stdout.write(JSON.stringify(out));"
    )
    edge = json.loads(
        subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True
        ).stdout
    )
    for path in PATHS:
        assert (nginx_route(path) == "api") == (edge[path] == "api"), path


def test_metrics_is_blocked_with_the_edge_message() -> None:
    assert nginx_route("/metrics") == nginx_route("/metrics/x") == "blocked"
    assert "Not found. The API is described at /docs." in CONF
    assert "Not found. The API is described at /docs." in EDGE


def test_page_csp_matches_the_meta_tag_plus_frame_ancestors() -> None:
    meta = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', HTML).group(1)
    header = re.search(r'add_header Content-Security-Policy "([^"]+)"', CONF).group(1)
    assert header == f"{meta}; frame-ancestors 'none'"


def test_security_headers_only_on_the_page() -> None:
    """Swagger UI at /docs loads scripts from a CDN; the page's CSP would break it."""
    page = re.search(r"location / \{(.*?)\n    \}", CONF, re.S).group(1)
    assert "Content-Security-Policy" in page and "nosniff" in page
    assert CONF.count("Content-Security-Policy") == 1  # nowhere else


def test_api_gets_the_viewer_address_like_cloudfront() -> None:
    assert (
        'proxy_set_header CloudFront-Viewer-Address "$remote_addr:$remote_port"'
        in PROXY_CONF
    )
    assert CONF.count("include /etc/nginx/api_proxy.conf;") == CONF.count("proxy_pass")
    assert "proxy_read_timeout 60s" in CONF  # CloudFront's origin_read_timeout


def test_cdn_timeout_is_the_same() -> None:
    assert "origin_read_timeout = 60" in (ROOT / "infra" / "cdn.tf").read_text()


class AnyTag(yaml.SafeLoader):
    """Loads compose files with tags such as !override as plain values."""


AnyTag.add_multi_constructor(
    "!", lambda loader, suffix, node: loader.construct_sequence(node)
)


def compose(name: str) -> dict:
    return yaml.load((ROOT / name).read_text(), Loader=AnyTag)


def test_compose_mounts_existing_files_for_web() -> None:
    web = compose("docker-compose.yml")["services"]["web"]
    for volume in web["volumes"]:
        host = volume.split(":")[0]
        assert (ROOT / host).exists(), host
    assert web["ports"] == ["127.0.0.1:8080:80"]  # never exposed beyond this machine


def test_production_never_starts_web() -> None:
    web = compose("docker-compose.prod.yml")["services"]["web"]
    assert web["profiles"] == ["local-only"]
    deploy = (ROOT / "deploy" / "deploy.sh").read_text()
    assert "--profile" not in deploy and "local-only" not in deploy


def test_nginx_config_is_not_published_with_the_page() -> None:
    """frontend/ is uploaded to S3 as is; only the page's files belong there."""
    assert sorted(p.name for p in (ROOT / "frontend").iterdir()) == [
        "app.js",
        "index.html",
        "style.css",
    ]

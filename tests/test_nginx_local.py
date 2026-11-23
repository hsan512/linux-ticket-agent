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


# --- production (CloudFront, infra/cdn.tf + infra/site.tf) matches the same rules ---

CDN = (ROOT / "infra" / "cdn.tf").read_text()
SITE = (ROOT / "infra" / "site.tf").read_text()


def test_cloudfront_csp_matches_nginx_and_the_meta_tag() -> None:
    nginx = re.search(r'add_header Content-Security-Policy "([^"]+)"', CONF).group(1)
    cloudfront = re.search(r'content_security_policy\s*=\s*"([^"]+)"', SITE).group(1)
    assert cloudfront == nginx


def cdn_api_paths() -> list[str]:
    block = re.search(r"api_paths\s*=\s*\[([^\]]*)\]", CDN).group(1)
    return re.findall(r'"([^"]+)"', block)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_cloudfront_api_paths_match_the_edge_function() -> None:
    """A path routed to the API must pass the edge function, and every path the edge
    function lets through must be routed to the API (else it falls to S3)."""

    def to_uri(pattern: str) -> str:
        return pattern.replace("*", "x")  # a concrete path under the pattern

    uris = [to_uri(p) for p in cdn_api_paths()]
    script = EDGE.replace("${online}", "true") + (
        f"\nconst out = {{}};\nfor (const uri of {json.dumps(uris)}) {{"
        "\n  const r = handler({request: {uri, method: 'GET', headers: {}}});"
        "\n  out[uri] = r.statusCode ? 'blocked' : 'api';\n}"
        "\nprocess.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    assert set(json.loads(result.stdout).values()) == {"api"}
    for path in PATHS:  # nginx's API paths are covered by a CloudFront pattern
        if nginx_route(path) == "api":
            assert any(
                path == p or (p.endswith("/*") and path.startswith(p[:-1]))
                for p in cdn_api_paths()
            ), path


def cdn_blocked_paths() -> list[str]:
    block = re.search(r"blocked_paths\s*=\s*\[([^\]]*)\]", CDN).group(1)
    return re.findall(r'"([^"]+)"', block)


def covered(path: str, patterns: list[str]) -> bool:
    return any(
        path == p or (p.endswith("/*") and path.startswith(p[:-1])) for p in patterns
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_cloudfront_blocks_metrics_with_the_edge_404() -> None:
    """Every path nginx blocks must have a CloudFront behaviour that runs the edge
    function; otherwise it falls to S3 and gets a 403 XML (seen in the live test)."""
    blocked = cdn_blocked_paths()
    assert "for_each = concat(local.api_paths, local.blocked_paths)" in CDN
    for path in PATHS:
        if nginx_route(path) == "blocked":
            assert covered(path, blocked), path
    uris = [p.replace("*", "x") for p in blocked]
    script = EDGE.replace("${online}", "true") + (
        f"\nconst out = {{}};\nfor (const uri of {json.dumps(uris)}) {{"
        "\n  const r = handler({request: {uri, method: 'GET', headers: {}}});"
        "\n  out[uri] = r.statusCode;"
        "\n}\nprocess.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    assert set(json.loads(result.stdout).values()) == {404}


def test_page_headers_only_on_the_default_behaviour() -> None:
    default = re.search(r"default_cache_behavior \{(.*?)\n  \}", CDN, re.S).group(1)
    ordered = re.search(
        r'dynamic "ordered_cache_behavior" \{(.*?)\n  \}', CDN, re.S
    ).group(1)
    assert (
        "response_headers_policy_id" in default
        and 'target_origin_id           = "site"' in default
    )
    assert "response_headers_policy_id" not in ordered  # Swagger at /docs needs its CDN
    assert "function_association" in ordered and "function_association" not in default


def test_site_bucket_is_private_and_read_only_by_this_distribution() -> None:
    assert SITE.count("= true") >= 4 and "restrict_public_buckets = true" in SITE
    assert '"AWS:SourceArn"' in SITE and "aws_cloudfront_distribution.api.arn" in SITE
    assert '"s3:GetObject"' in SITE and "s3:PutObject" not in SITE

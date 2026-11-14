"""Tests for the monitoring config: Grafana provisioning, the dashboard, and the
Prometheus alert rules agree with each other, with docker-compose.yml, and with the
metrics the API really exports. A misspelled metric name in a panel or rule gives no
error anywhere, only an empty panel or an alert that never fires, so it is caught here.
No Docker or network needed.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import api.main  # noqa: F401  (importing it registers every metric the API exports)

ROOT = Path(__file__).resolve().parent.parent
GRAFANA = ROOT / "monitoring" / "grafana"
DATASOURCE = GRAFANA / "provisioning" / "datasources" / "datasource.yml"
PROVIDER = GRAFANA / "provisioning" / "dashboards" / "provider.yml"
DASHBOARD = GRAFANA / "dashboards" / "ticket-agent.json"
ALERTS = ROOT / "monitoring" / "prometheus" / "alerts.yml"
COMPOSE = ROOT / "docker-compose.yml"

# made by Prometheus itself, not by the API: scrape health and active alerts
PROMETHEUS_SERIES = {"up", "ALERTS", "ALERTS_FOR_STATE"}
PROMQL_KEYWORDS = {
    "and", "or", "unless", "by", "without", "on", "ignoring", "group_left",
    "group_right", "offset", "bool", "inf", "nan",
}  # fmt: skip
SUFFIXES = {
    "counter": ["_total"],
    "histogram": ["_bucket", "_sum", "_count"],
    "summary": ["", "_sum", "_count"],
    "gauge": [""],
}


def exported_series() -> set[str]:
    """Every series name the API can expose at /metrics, from the metric families in
    the registry (families with labels have no samples until first used, so samples
    alone would miss them). The instrumentator registers its HTTP metrics only when
    the app serves its first request, so one is sent first."""
    TestClient(api.main.app).get("/health")
    names = set()
    for family in REGISTRY.collect():
        for suffix in SUFFIXES.get(family.type, [""]):
            names.add(family.name + suffix)
    return names


def metric_names(expr: str) -> set[str]:
    """Metric names used in a PromQL expression: identifiers that are not functions,
    keywords, label names, label values, durations, or Grafana variables."""
    expr = re.sub(r'"(?:[^"\\]|\\.)*"', "", expr)  # string literals (label values)
    expr = re.sub(r"\{[^}]*\}", "", expr)  # label matchers
    expr = re.sub(r"\[[^\]]*\]", "", expr)  # range windows, incl. $__rate_interval
    expr = re.sub(
        r"\b(by|without|on|ignoring)\s*\([^)]*\)", "", expr
    )  # grouping labels
    expr = re.sub(r"\$\w+", "", expr)  # other Grafana variables
    found = re.findall(r"(?<![\w.])([A-Za-z_:][\w:]*)\b(?!\s*\()", expr)
    return {name for name in found if name.lower() not in PROMQL_KEYWORDS}


def dashboard_queries() -> list[tuple[str, dict]]:
    """(panel title, query) for every query of every panel in the v2 dashboard."""
    spec = json.loads(DASHBOARD.read_text())["spec"]
    return [
        (element["spec"]["title"], query["spec"]["query"])
        for element in spec["elements"].values()
        for query in element["spec"]["data"]["spec"]["queries"]
    ]


def alert_exprs() -> list[tuple[str, str]]:
    groups = yaml.safe_load(ALERTS.read_text())["groups"]
    return [(rule["alert"], rule["expr"]) for g in groups for rule in g["rules"]]


def grafana_mounts() -> dict[str, str]:
    """Grafana's bind mounts in docker-compose.yml: container path -> host path."""
    volumes = yaml.safe_load(COMPOSE.read_text())["services"]["grafana"]["volumes"]
    mounts = {}
    for volume in volumes:
        host, container, *_ = volume.split(":")
        if host.startswith("./"):
            mounts[container] = host
    return mounts


# --- the metric-name extractor itself ------------------------------------------


@pytest.mark.parametrize(
    ("expr", "names"),
    [
        ('up{job="ticket-api"}', {"up"}),
        (
            "sum by (outcome) (increase(tickets_total[$__rate_interval]))",
            {"tickets_total"},
        ),
        (
            "histogram_quantile(0.95, sum by (le) (rate(x_bucket[30m]))) > 60",
            {"x_bucket"},
        ),
        (
            'a_total{outcome=~"answered|escalated"} / b_total and c >= 5',
            {"a_total", "b_total", "c"},
        ),
        ('ALERTS{alertstate="firing"}', {"ALERTS"}),
    ],
)  # fmt: skip
def test_metric_names_finds_only_metrics(expr: str, names: set[str]) -> None:
    assert metric_names(expr) == names


# --- Grafana provisioning --------------------------------------------------------


def test_datasource_points_at_prometheus_in_compose() -> None:
    [source] = yaml.safe_load(DATASOURCE.read_text())["datasources"]
    assert source["type"] == "prometheus" and source["uid"] == "prometheus"
    assert (
        source["url"] == "http://prometheus:9090"
    )  # compose service name, not localhost
    assert "prometheus" in yaml.safe_load(COMPOSE.read_text())["services"]


def test_provider_path_is_where_compose_mounts_the_dashboard() -> None:
    [provider] = yaml.safe_load(PROVIDER.read_text())["providers"]
    path = provider["options"]["path"]
    mounted = {
        Path(c).parent.as_posix() if c.endswith(".json") else c
        for c in grafana_mounts()
    }
    assert path in mounted, f"provider reads {path}, compose mounts {sorted(mounted)}"


def test_compose_mounts_each_grafana_file_that_exists() -> None:
    for container, host in grafana_mounts().items():
        assert (ROOT / host).exists(), f"{host} is mounted at {container} but missing"


def test_grafana_password_comes_from_the_environment() -> None:
    env = yaml.safe_load(COMPOSE.read_text())["services"]["grafana"]["environment"]
    assert env["GF_SECURITY_ADMIN_PASSWORD"].startswith(
        "${"
    )  # never written in the repo


# --- the dashboard ---------------------------------------------------------------


def test_dashboard_has_panels_with_queries() -> None:
    queries = dashboard_queries()
    assert len({title for title, _ in queries}) >= 9
    assert all(query["spec"].get("expr", "").strip() for _, query in queries)


def test_every_panel_uses_the_provisioned_datasource() -> None:
    [source] = yaml.safe_load(DATASOURCE.read_text())["datasources"]
    for title, query in dashboard_queries():
        assert query["group"] == "prometheus", title
        assert query["datasource"]["name"] == source["uid"], title


def test_every_panel_metric_exists() -> None:
    known = exported_series() | PROMETHEUS_SERIES
    for title, query in dashboard_queries():
        missing = metric_names(query["spec"]["expr"]) - known
        assert not missing, f"panel {title!r} queries unknown metrics {missing}"


# --- the alert rules -------------------------------------------------------------


def test_every_alert_metric_exists() -> None:
    known = exported_series() | PROMETHEUS_SERIES
    for alert, expr in alert_exprs():
        missing = metric_names(expr) - known
        assert not missing, f"alert {alert} queries unknown metrics {missing}"

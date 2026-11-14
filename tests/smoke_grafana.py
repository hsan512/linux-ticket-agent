"""Smoke test for Grafana provisioning: run against a live Grafana (docker compose)
and check that Grafana itself accepted our files, which the unit tests in
test_monitoring_config.py cannot prove.

Usage: GRAFANA_ADMIN_PASSWORD=... python tests/smoke_grafana.py [http://localhost:3000]

Standard library only, so CI needs no pip install. Not collected by pytest (the name
does not start with test_): it needs a running Grafana.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DASHBOARD_FILE = Path(__file__).resolve().parent.parent / (
    "monitoring/grafana/dashboards/ticket-agent.json"
)
DATASOURCE_UID = "prometheus"
STARTUP_TIMEOUT_S = 120


def get(base: str, path: str, auth: str) -> dict | list:
    request = urllib.request.Request(
        base + path, headers={"Authorization": f"Basic {auth}"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def wait_until_provisioned(base: str, auth: str, title: str) -> None:
    """Grafana's API answers before provisioning has finished (on a slow CI runner
    the data source was still a 404), so wait until the data source and the
    dashboard both exist. Whether they are correct is checked afterwards."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while True:
        try:
            get(base, f"/api/datasources/uid/{DATASOURCE_UID}", auth)
            titles = [d["title"] for d in get(base, "/api/search?type=dash-db", auth)]
            if title in titles:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
            pass  # not up yet, or the data source not provisioned yet (HTTP 404)
        if time.monotonic() > deadline:
            sys.exit(
                f"FAIL: data source {DATASOURCE_UID!r} and dashboard {title!r} not "
                f"provisioned after {STARTUP_TIMEOUT_S}s at {base}"
            )
        time.sleep(2)


def check(condition: bool, message: str) -> None:
    print(("ok    " if condition else "FAIL  ") + message)
    if not condition:
        sys.exit(1)


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000").rstrip("/")
    password = os.environ.get("GRAFANA_ADMIN_PASSWORD")
    if not password:
        sys.exit("FAIL: set GRAFANA_ADMIN_PASSWORD")
    auth = base64.b64encode(f"admin:{password}".encode()).decode()
    spec = json.loads(DASHBOARD_FILE.read_text())["spec"]
    wait_until_provisioned(base, auth, spec["title"])

    for _ in range(15):  # Prometheus may still be starting: retry for up to ~30 s
        try:
            health = get(base, f"/api/datasources/uid/{DATASOURCE_UID}/health", auth)
        except urllib.error.HTTPError as error:  # Grafana answers 400 while it fails
            health = json.load(error)
        if health.get("status") == "OK":
            break
        time.sleep(2)
    check(
        health.get("status") == "OK",
        f"data source '{DATASOURCE_UID}' reaches Prometheus ({health.get('message')})",
    )

    found = [
        d
        for d in get(base, "/api/search?type=dash-db", auth)
        if d["title"] == spec["title"]
    ]
    check(len(found) == 1, f"exactly one dashboard titled {spec['title']!r}")

    loaded = get(base, f"/api/dashboards/uid/{found[0]['uid']}", auth)
    meta = loaded["meta"]
    check(meta.get("provisioned") is True, "dashboard is provisioned, not UI-made")
    check(
        meta.get("provisionedExternalId") == DASHBOARD_FILE.name,
        f"provisioned from {DASHBOARD_FILE.name}",
    )
    expected = len(spec["elements"])
    panels = loaded["dashboard"].get("panels") or []
    check(len(panels) == expected, f"{len(panels)} of {expected} panels loaded")
    print("Grafana smoke test passed")


if __name__ == "__main__":
    main()

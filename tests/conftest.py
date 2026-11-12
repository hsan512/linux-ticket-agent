"""Fixtures shared by the API tests (test_api.py, test_metrics.py)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main
from api.budget import DailyBudget
from api.handoff import save_handoff
from api.main import app


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

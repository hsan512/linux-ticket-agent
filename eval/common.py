"""Small I/O helpers shared by the eval scripts."""

from __future__ import annotations

import json
from pathlib import Path

from agent.config import PROCESSED_DIR


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_benchmark(path: Path, split: str) -> list[dict]:
    return [r for r in load_jsonl(path) if r["split"] == split]


def latest_by_question(records: list[dict]) -> dict[int, dict]:
    """Keep the last record per question (a resumed run may retry a ticket that errored)."""
    return {r["question_id"]: r for r in records}


def load_known_commands(path: Path = PROCESSED_DIR / "known_commands.json") -> set[str]:
    return set(json.loads(path.read_text()))

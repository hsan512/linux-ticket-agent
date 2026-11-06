"""Tests for the hand-off record (pure: no file is written)."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.schemas import AgentDraft, Evidence
from api.handoff import base_record, handoff_record, save_handoff
from eval.common import load_jsonl

DRAFT = AgentDraft(answer="Run it [D2].", commands=["rm -rf /"], citations=["D2"], confidence="high")
EVIDENCE = [Evidence(id=i, source="docs", title=i, url=f"https://x/{i}", text=f"text of {i}", score=0.5)
            for i in ("D1", "D2", "H1")]


def _record(**state: object) -> dict:
    return handoff_record("abc", "ticket text", {"draft": DRAFT, "evidence": EVIDENCE, **state}, "v4")


def test_failed_check_keeps_only_the_exception_name() -> None:
    record = _record(escalation_kind="unchecked", escalation_reason="validation could not run: TimeoutExpired(...)",
                     validation_error="TimeoutExpired(cmd=['bash', '-n'], timeout=5.0)")
    assert record["escalation_reason"] == "validation could not run: TimeoutExpired"
    assert "cmd=" not in json.dumps(record)


def test_other_kinds_keep_the_escalate_node_reason() -> None:
    reason = "blocking issues left after 2 revisions:\n- `rm -rf /` (catastrophic): ..."
    assert _record(escalation_kind="unsafe", escalation_reason=reason)["escalation_reason"] == reason


def test_record_has_what_the_human_needs() -> None:
    record = _record(escalation_kind="not_confident", escalation_reason="confidence=medium")
    assert (record["ticket_id"], record["ticket"], record["version"]) == ("abc", "ticket text", "v4")
    assert record["draft"]["commands"] == ["rm -rf /"]
    assert [(c["id"], c["text"]) for c in record["citations"]] == [("D2", "text of D2")]   # cited only, with text
    assert record["created_at"].endswith("+00:00")                                          # UTC
    assert json.loads(json.dumps(record)) == record                                         # plain JSON, no objects


def test_record_without_agent_output() -> None:
    record = base_record("abc", "a very long ticket", "too_long", "question has 5000 characters", "v4")
    assert record["draft"] is None and record["citations"] == []
    assert set(record) == set(_record(escalation_kind="unsafe", escalation_reason="r"))   # same fields either way


def test_save_appends_one_line_per_record(tmp_path: Path) -> None:
    path = tmp_path / "new_dir" / "handoff.jsonl"                     # folder does not exist yet
    save_handoff({"ticket_id": "a", "ticket": "ÿ 中文"}, path)
    save_handoff({"ticket_id": "b"}, path)
    assert path.read_text(encoding="utf-8").count("\n") == 2
    assert [r["ticket_id"] for r in load_jsonl(path)] == ["a", "b"]
    assert "中文" in path.read_text(encoding="utf-8")                  # ensure_ascii=False keeps text readable


def test_concurrent_saves_keep_every_record(tmp_path: Path) -> None:
    # Not a test of the lock itself: on Linux it also passes without the lock (checked), since an
    # append-mode write of one line is usually a single system call. The lock is the guarantee.
    path = tmp_path / "handoff.jsonl"
    big = "x" * 200_000
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: save_handoff({"ticket_id": str(i), "ticket": big}, path), range(64)))
    records = load_jsonl(path)                                        # raises if any line is broken
    assert sorted(int(r["ticket_id"]) for r in records) == list(range(64))

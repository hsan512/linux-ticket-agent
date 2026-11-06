"""The human hand-off queue: one record per escalated ticket, for the person who answers it.

Not a log: it holds what that person needs to answer the asker (the ticket, the agent's draft,
the passages it cited). Internal error details stay in the server log.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from agent.config import ROOT
from agent.state import AgentState
from agent.tools.retrieval import cited_evidence

HANDOFF_PATH = ROOT / "data" / "handoff" / "handoff.jsonl"  # JSON Lines; holds real tickets, never committed

# endpoints run in a thread pool, so two escalated tickets can be saved at the same moment
_lock = threading.Lock()


def base_record(ticket_id: str, ticket: str, kind: str, reason: str, version: str) -> dict:
    """The fields every hand-off has, with no agent output: used as is when the agent never ran
    (e.g. a ticket longer than the agent was tested on), and filled in by handoff_record."""
    return {
        "ticket_id": ticket_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "ticket": ticket,
        "escalation_kind": kind,
        "escalation_reason": reason,
        "draft": None,
        "citations": [],
    }


def handoff_record(ticket_id: str, ticket: str, state: AgentState, version: str) -> dict:
    """The record for a ticket the agent escalated, with its draft and the passages it cited.
    A failed check keeps only the exception name (e.g. TimeoutExpired); the full error is for the
    server log."""
    kind = state["escalation_kind"]
    if kind == "unchecked":
        reason = f"validation could not run: {state['validation_error'].split('(', 1)[0]}"
    else:
        reason = state["escalation_reason"]
    cited = cited_evidence(state["draft"], state.get("evidence", []))
    return base_record(ticket_id, ticket, kind, reason, version) | {
        "draft": state["draft"].model_dump(),
        "citations": [e.model_dump() for e in cited],
    }


def save_handoff(record: dict, path: Path = HANDOFF_PATH) -> None:
    """Append `record` to the queue as one JSON line. Errors are not caught: a ticket that could
    not be queued must fail the request, not be reported as handed off."""
    line = json.dumps(record, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:  # one writer at a time, so lines never interleave
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
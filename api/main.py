"""HTTP API for the troubleshooting agent.

The graph and its dependencies are built once at startup and shared by all requests: they are
read-only, while each ticket's state lives only inside its own graph run.
"""

from __future__ import annotations

import logging
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import State
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from agent.config import PROCESSED_DIR
from agent.graph import Features, build_graph, run_ticket
from agent.state import AgentContext, AgentState, EscalationKind, Status
from agent.tools.packages import CommandPackageLookup, load_command_packages
from agent.tools.retrieval import Retriever, cited_evidence
from agent.trace import summarize
from api.handoff import base_record, handoff_record, save_handoff

logger = logging.getLogger(__name__)

# One line per ticket, keyed by ticket_id. Never the ticket text: it is user data, and the hand-off
# queue already keeps it for escalated tickets.
logger = logging.getLogger(__name__)

# the benchmark's limit (eval/make_benchmark.py): longer tickets were never measured
MAX_QUESTION_CHARS = 4000
HandoffKind = EscalationKind | Literal["too_long"]  # too_long is decided here, before the agent runs

# shown to the asker instead of escalation_reason, which may hold internal details
PUBLIC_REASON: dict[HandoffKind, str] = {
    "unchecked": "The proposed answer could not be checked automatically.",
    "unsafe": "The proposed commands may be unsafe.",
    "not_confident": "The agent is not confident enough in its answer.",
    "too_long": "The question is too long to be processed automatically.",
}

# Per client IP, so one sender cannot run up the OpenAI bill (a ticket costs about $0.013). Counts
# live in this process's memory: they reset on restart. Behind a load balancer every request has its
# IP, so uvicorn must then run with --proxy-headers to see the real client.
TICKET_RATE_LIMIT = "5/minute;30/day"
limiter = Limiter(key_func=get_remote_address)

# the configuration measured on test (eval/results/findings.md, item 31)
FEATURES = Features(grade_evidence=True, validate_revise=True, escalation_rule="confidence_not_high")


class Citation(BaseModel):
    """A passage the answer cites, with its source for attribution (Stack Exchange is CC BY-SA)."""

    id: str = Field(description="Citation ID as written in the answer, e.g. D1 or H2.")
    title: str
    url: str = Field(description="The man page, manual, or original Stack Exchange post.")


class TicketResponse(BaseModel):
    """The agent's result for one ticket: an answer with citations, or a hand-off to a human."""

    ticket_id: str = Field(description="Identifies the ticket in the human hand-off queue.")
    status: Status
    answer: str | None = Field(
        default=None, description="Set when answered; warnings about risky commands come first."
    )
    citations: list[Citation] = Field(default=[], description="Passages the answer relies on; empty when escalated.")
    escalation_reason: str | None = Field(
        default=None, description="Why a human will take the ticket; set when escalated."
    )


class TicketRequest(BaseModel):
    """The request body for one ticket: a title and the question text."""

    # hard limits (422): far beyond any real question; Stack Exchange's own limits are 150 and 30,000
    title: str = Field(max_length=200, description="A one-line summary of the problem.")
    question_text: str = Field(max_length=30_000, description="The full question: what was tried, what happened.")


@asynccontextmanager
async def lifespan_context(app: FastAPI) -> AsyncIterator[None]:
    """Build the graph and its dependencies once, when the server starts. Nothing needs closing
    at shutdown: the process exit frees them."""
    # uvicorn configures only its own loggers; without this, this module's INFO lines are dropped
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # else one line per OpenAI and Qdrant request
    command_packages = load_command_packages(PROCESSED_DIR / "command_packages.json")
    command_lookup = CommandPackageLookup(command_packages)
    app.state.context = AgentContext(retriever=Retriever(), command_lookup=command_lookup)
    app.state.graph = build_graph(FEATURES)
    logger.info("ready: %s", FEATURES.version)
    yield


app = FastAPI(title="Troubleshooting Agent API", version=FEATURES.version, lifespan=lifespan_context)
app.state.limiter = limiter  # slowapi looks for it here
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # over the limit -> 429


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness check: answering means startup finished (package table loaded, Qdrant collections
    found, graph built). It checks nothing again, so it does not prove Qdrant or OpenAI are
    reachable now."""
    return {"status": "ok", "version": FEATURES.version}


def log_ticket(ticket_id: str, state: AgentState, wall_s: float) -> None:
    """One INFO line per agent run: outcome, cost, and time. A check that could not run also gets
    a WARNING with the full error, which the hand-off record and the asker never see."""
    summary = summarize(state, wall_s)
    outcome = f"escalated {state['escalation_kind']}" if state["status"] == "escalated" else state["status"]
    logger.info("ticket %s: %s, $%.4f, %.1fs, %d LLM calls, %d revisions", ticket_id, outcome,
                summary["cost_usd"], wall_s, summary["usage"]["llm_calls"], state.get("revise_attempts", 0))
    if state.get("validation_error"):
        logger.warning("ticket %s: validation could not run: %s", ticket_id, state["validation_error"])


@app.post("/tickets", response_model=TicketResponse)
@limiter.limit(TICKET_RATE_LIMIT)  # checked before the body runs, so a 429 is never caught below
def create_ticket(body: TicketRequest, request: Request) -> TicketResponse:
    """Run the agent on one ticket, or hand it to a human. Any failure becomes a 500 with a
    generic message and the ticket ID to quote; the details go to the server log only."""
    ticket_id = uuid.uuid4().hex
    try:
        return handle_ticket(ticket_id, body, request.app.state)
    except Exception:
        logger.exception("ticket %s: failed", ticket_id)  # with the stack trace
        raise HTTPException(status_code=500, detail=f"Internal error. Reference: {ticket_id}") from None


def handle_ticket(ticket_id: str, body: TicketRequest, app_state: State) -> TicketResponse:
    """The work behind create_ticket. Title and question are joined as in the evaluation. A
    question longer than MAX_QUESTION_CHARS goes to a human without running the agent, since the
    agent was never measured on one."""
    ticket = f"{body.title}\n\n{body.question_text}"
    question_chars = len(body.question_text)
    if question_chars > MAX_QUESTION_CHARS:
        reason = f"question has {question_chars} characters; the agent was tested up to {MAX_QUESTION_CHARS}"
        save_handoff(base_record(ticket_id, ticket, "too_long", reason, FEATURES.version))
        logger.info("ticket %s: escalated too_long, %d question chars, agent not run", ticket_id, question_chars)
        return TicketResponse(
            ticket_id=ticket_id,
            status="escalated",
            escalation_reason=PUBLIC_REASON["too_long"],
        )
    start = time.perf_counter()
    state = run_ticket(app_state.graph, ticket, app_state.context)
    log_ticket(ticket_id, state, time.perf_counter() - start)
    if state["status"] == "escalated":
        save_handoff(handoff_record(ticket_id, ticket, state, FEATURES.version))
        return TicketResponse(
            ticket_id=ticket_id,
            status="escalated",
            escalation_reason=PUBLIC_REASON[state["escalation_kind"]],
        )
    cited = cited_evidence(state["draft"], state.get("evidence", []))
    citations = [Citation(id=e.id, title=e.title, url=e.url) for e in cited]
    return TicketResponse(
        ticket_id=ticket_id,
        status="answered",
        answer=state.get("final_answer"),
        citations=citations,
    )
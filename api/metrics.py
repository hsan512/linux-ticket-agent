"""Prometheus metrics for the API, exposed at GET /metrics and pulled by Prometheus.

The metric objects are module-level and register themselves in prometheus_client's
default registry, so defining one here is enough for it to appear at /metrics. Values
live in this process's memory: they reset when the API restarts (rate() handles that).

Labels take only the fixed values named in each description, never ticket IDs or ticket
text: every distinct label value is a separate series in Prometheus, and tickets are
user data. The label values are created up front in api/main.py, so each series shows
0 before its first ticket.

/metrics is for Prometheus only: keep it off the public internet (compose binds the API
to 127.0.0.1; a deployment must block the path at its proxy).
"""

from prometheus_client import Counter, Gauge, Histogram

TICKETS = Counter(
    "tickets_total",
    "Tickets handled by POST /tickets, by outcome: answered, escalated, too_long, "
    "budget_exhausted, error.",
    ["outcome"],
)

ESCALATIONS = Counter(
    "escalations_total",
    "Tickets escalated to a human, by kind: unchecked, unsafe, not_confident, "
    "too_long.",
    ["kind"],
)

TICKET_DURATION = Histogram(
    "ticket_duration_seconds",
    "Wall time of one agent run, in seconds.",
    buckets=(1, 2, 5, 10, 15, 20, 30, 45, 60, 120),
)

TICKET_COST = Histogram(
    "ticket_cost_usd",
    "OpenAI cost of one agent run, in USD (generation + query embedding).",
    buckets=(0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0.1),
)

REVISIONS = Counter(
    "ticket_revisions_total",
    "Draft revisions made by the revise node after validate found blocking issues.",
)

BUDGET_SPENT = Gauge(
    "daily_budget_spent_usd",
    "OpenAI spending counted against the daily budget today (UTC), in USD.",
)

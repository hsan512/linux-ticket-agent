"""Escalation rules: hand a ticket to a human instead of showing a likely-wrong answer.

Shared by the escalate routing in agent/graph.py and by eval/apply_escalation.py
(offline replay on saved predictions), so both make exactly the same decision. Inputs
come from the draft's self-reported confidence and grade_evidence's final verdict; no
extra LLM call is made.
"""

from __future__ import annotations

from collections.abc import Callable

# rule(confidence, evidence_insufficient) -> escalate?
RULES: dict[str, Callable[[str, bool], bool]] = {
    "confidence_low": lambda confidence, insufficient: confidence == "low",
    "confidence_not_high": lambda confidence, insufficient: confidence != "high",
    "insufficient": lambda confidence, insufficient: insufficient,
    "not_high_or_insufficient": lambda confidence, insufficient: (
        confidence != "high" or insufficient
    ),
}


def should_escalate(rule: str, confidence: str, insufficient: bool) -> bool:
    """Apply the named rule; raises KeyError for a rule not in RULES."""
    return RULES[rule](confidence, insufficient)


def escalation_reason(confidence: str, insufficient: bool) -> str:
    """The inputs every rule looks at, recorded so a human can see why the ticket was
    handed off."""
    return f"confidence={confidence}, evidence_insufficient={insufficient}"

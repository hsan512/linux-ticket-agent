"""Fake graph states shared by the API tests: no OpenAI, Qdrant, or package table."""

from agent.schemas import AgentDraft, Evidence


class FakeGraph:
    """Stands in for the compiled graph: returns a fixed final state and records the
    input."""

    def __init__(self, state: dict) -> None:
        self.state = state
        self.inputs: list[dict] = []

    def invoke(self, state: dict, **kwargs: object) -> dict:
        self.inputs.append(state)
        return self.state


def evidence(id_: str) -> Evidence:
    return Evidence(
        id=id_,
        source="docs",
        title=f"title {id_}",
        url=f"https://x/{id_}",
        text="secret text",
        score=0.5,
    )


DRAFT = AgentDraft(
    answer="Use ls [D2].", commands=["ls"], citations=["D2"], confidence="high"
)
TRACE = [
    {
        "node": "draft",
        "model": "m",
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 0,
        "cost_usd": 0.0012,
        "latency_s": 1.0,
    }
]
ANSWERED = {
    "status": "answered",
    "draft": DRAFT,
    "final_answer": "WARNING...\n\nUse ls [D2].",
    "evidence": [evidence("D1"), evidence("D2"), evidence("H1")],
    "trace": TRACE,
}


def escalated(kind: str) -> dict:
    return ANSWERED | {
        "status": "escalated",
        "escalation_kind": kind,
        "escalation_reason": "validation could not run: TimeoutExpired(cmd=['bash', "
        "'-n'])",
        "validation_error": "TimeoutExpired(cmd=['bash', '-n'], timeout=5.0)",
    }


class BrokenGraph:
    """A graph whose run fails, as when OpenAI is down."""

    def invoke(self, state: dict, **kwargs: object) -> dict:
        raise RuntimeError("openai: secret internal detail")

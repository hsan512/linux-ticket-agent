"""LLM-as-a-Judge for a run's predictions (judge model differs from the generator).

Two separate judgments per ticket:
- correctness:  candidate vs. the community-accepted answer (1-5; "correct" = score >=
  4)
- faithfulness: are the candidate's claims supported by the passages it cites?
Appends to eval/runs/<run_id>/judgments.jsonl; resumes and respects --budget-usd.
"""

from __future__ import annotations

import argparse
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent.config import JUDGE_MODEL, PROCESSED_DIR, RUNS_DIR
from agent.llm import invoke_structured
from eval.common import latest_by_question, load_jsonl
from eval.runner import run_jobs

JUDGE_REASONING_EFFORT = "low"
CORRECT_THRESHOLD = 4

CORRECTNESS_PROMPT = """You grade answers to Unix/Linux support questions.
You get the question, a reference answer (accepted by the asker on Unix & Linux Stack \
Exchange), and a candidate answer. Judge whether the candidate would solve the asker's \
problem.
A different approach from the reference is fine if it is correct and would work.
Penalize wrong commands or options, unsafe advice, and missing essential steps.
Scale:
5 = solves the problem; as good as or better than the reference
4 = solves the problem with minor omissions or imprecision
3 = partially solves it, or right idea with a significant error
2 = mostly wrong or off-target, with a little useful content
1 = wrong, irrelevant, or harmful"""

FAITHFULNESS_PROMPT = """You check whether an answer is grounded in the passages it \
cites.
supported = every key claim and command in the answer is backed by the cited passages
partially_supported = some key claims or commands are backed, others are not
unsupported = the cited passages do not back the answer's key claims"""


class CorrectnessVerdict(BaseModel):
    reasoning: str = Field(
        description="At most 3 sentences, written before choosing the score."
    )
    score: Literal[1, 2, 3, 4, 5]


class FaithfulnessVerdict(BaseModel):
    reasoning: str = Field(description="At most 3 sentences.")
    label: Literal["supported", "partially_supported", "unsupported"]


def judge_correctness(item: dict, pred: dict) -> tuple[CorrectnessVerdict, float]:
    user = (
        f"Question:\n{item['title']}\n\n{item['question_text']}\n\n---\n"
        f"Reference answer:\n{item['answer_text']}\n\n---\nCandidate "
        f"answer:\n{pred['draft']['answer']}"
    )
    r = invoke_structured(
        JUDGE_MODEL,
        CorrectnessVerdict,
        [SystemMessage(CORRECTNESS_PROMPT), HumanMessage(user)],
        JUDGE_REASONING_EFFORT,
    )
    return r.parsed, r.cost_usd


def cited_ids(pred: dict) -> list[str]:
    """IDs in the citations list or written inline in the answer (e.g. "(D4)"), that
    were retrieved."""
    ids = list(
        dict.fromkeys(
            pred["draft"]["citations"]
            + re.findall(r"\b[DHP]\d+\b", pred["draft"]["answer"])
        )
    )
    return [c for c in ids if c in pred["evidence_text"]]


def judge_faithfulness(pred: dict) -> tuple[FaithfulnessVerdict | None, float]:
    cited = cited_ids(pred)
    if not cited:
        return None, 0.0
    passages = "\n\n".join(f"[{c}]\n{pred['evidence_text'][c]}" for c in cited)
    user = f"Cited passages:\n\n{passages}\n\n---\nAnswer:\n{pred['draft']['answer']}"
    r = invoke_structured(
        JUDGE_MODEL,
        FaithfulnessVerdict,
        [SystemMessage(FAITHFULNESS_PROMPT), HumanMessage(user)],
        JUDGE_REASONING_EFFORT,
    )
    return r.parsed, r.cost_usd


def judge_one(item: dict, pred: dict) -> dict:
    correctness, c_cost = judge_correctness(item, pred)
    faithfulness, f_cost = judge_faithfulness(pred)
    return {
        "question_id": pred["question_id"],
        "judge_model": JUDGE_MODEL,
        "score": correctness.score,
        "correct": correctness.score >= CORRECT_THRESHOLD,
        "correctness_reasoning": correctness.reasoning,
        "faithfulness": faithfulness.label if faithfulness else "no_citations",
        "faithfulness_reasoning": faithfulness.reasoning if faithfulness else None,
        "cost_usd": c_cost + f_cost,
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=3.0)
    args = parser.parse_args()

    run_dir = RUNS_DIR / args.run_id
    preds = [
        p
        for p in latest_by_question(load_jsonl(run_dir / "predictions.jsonl")).values()
        if p["error"] is None
    ]
    items = {r["question_id"]: r for r in load_jsonl(PROCESSED_DIR / "benchmark.jsonl")}
    run_jobs(
        preds,
        lambda pred: judge_one(items[pred["question_id"]], pred),
        run_dir / "judgments.jsonl",
        args.budget_usd,
        args.workers,
        describe=lambda rec: f"score {rec['score']}",
    )


if __name__ == "__main__":
    main()

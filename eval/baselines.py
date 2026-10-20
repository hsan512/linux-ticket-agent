"""Single-call baselines, both using the same generator model as the agent:

- rag:      retrieve (docs + similar past questions) once, then answer in one LLM call
- llm_only: answer from the model's own knowledge, no retrieval (measures what retrieval adds)

Results are appended to eval/runs/<run_id>/predictions.jsonl, so an interrupted run resumes.
Stops submitting new tickets once the run's spend reaches --budget-usd.
"""

from __future__ import annotations

import argparse
import time

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import EMBED_MODEL, GENERATOR_MODEL, PROCESSED_DIR, cost_usd
from agent.llm import invoke_structured
from agent.schemas import DraftAnswer
from agent.tools.retrieval import Retriever, format_context
from eval.common import load_benchmark
from eval.runner import add_run_args, prepare_run, run_jobs

SYSTEM_PROMPT = """You are a Linux/Unix support engineer answering a user's ticket.
You are given context passages: documentation excerpts (D1, D2, ...) and past solved questions \
with their accepted answers (H1, H2, ...).
- Use the passages when they are relevant and cite the IDs you relied on.
- If the passages are not enough, you may use your own knowledge, but do not cite passages that \
do not support a claim.
- Prefer precise, minimal commands. Never invent commands or options.
- List every shell command you propose in `commands`, exactly as it should be run."""

LLM_ONLY_PROMPT = """You are a Linux/Unix support engineer answering a user's ticket.
- Prefer precise, minimal commands. Never invent commands or options.
- List every shell command you propose in `commands`, exactly as it should be run.
- You have no reference passages, so leave `citations` empty."""

PROMPTS = {"rag": SYSTEM_PROMPT, "llm_only": LLM_ONLY_PROMPT}

_enc = tiktoken.get_encoding("cl100k_base")


def ticket_text(item: dict) -> str:
    return f"{item['title']}\n\n{item['question_text']}"


def answer_ticket(item: dict, retriever: Retriever | None) -> dict:
    """Answer one ticket. retriever=None is the llm_only system."""
    start = time.perf_counter()
    query = ticket_text(item)
    if retriever is None:
        evidence, embed_cost = [], 0.0
        messages = [SystemMessage(LLM_ONLY_PROMPT), HumanMessage(f"Ticket:\n{query}")]
    else:
        evidence = retriever.search_docs(query) + retriever.search_similar(query)
        embed_cost = cost_usd(EMBED_MODEL, 2 * len(_enc.encode(query)))  # one query embedding per collection
        messages = [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(f"Context passages:\n\n{format_context(evidence)}\n\n---\nTicket:\n{query}"),
        ]
    retrieval_s = time.perf_counter() - start

    result = invoke_structured(GENERATOR_MODEL, DraftAnswer, messages)
    return {
        "question_id": item["question_id"],
        "split": item["split"],
        "draft": result.parsed.model_dump(),
        "evidence": [e.model_dump(exclude={"text"}) for e in evidence],
        "evidence_text": {e.id: e.text for e in evidence},
        "usage": {
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "llm_calls": 1,
        },
        "cost_usd": result.cost_usd + embed_cost,
        "latency_s": {"retrieval": retrieval_s, "llm": result.latency_s, "total": time.perf_counter() - start},
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=list(PROMPTS), required=True)
    add_run_args(parser)
    args = parser.parse_args()

    out_path = prepare_run(args, {
        "system": args.system, "generator": GENERATOR_MODEL,
        "embed": EMBED_MODEL if args.system == "rag" else None,
        "docs_k": 5, "history_k": 3, "prompt": PROMPTS[args.system],
    })
    retriever = Retriever() if args.system == "rag" else None
    items = load_benchmark(PROCESSED_DIR / "benchmark.jsonl", args.split)
    run_jobs(items[: args.limit] if args.limit else items, lambda item: answer_ticket(item, retriever),
             out_path, args.budget_usd, args.workers)


if __name__ == "__main__":
    main()

"""Run the LangGraph agent over a benchmark split, writing predictions in the same format as
eval/baselines.py (so judge.py, report.py, and compare.py work unchanged), plus the full trace."""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict, fields

from agent import prompts
from agent.config import EMBED_MODEL, GENERATOR_MODEL, PROCESSED_DIR
from agent.escalation import RULES
from agent.graph import MAX_STEPS, Features, build_graph, run_ticket
from agent.state import AgentContext, AgentState
from agent.tools.packages import load_command_packages, CommandPackageLookup
from agent.tools.retrieval import Retriever
from agent.trace import summarize
from eval.common import load_benchmark
from eval.runner import add_run_args, prepare_run, run_jobs


def to_record(item: dict, state: AgentState, wall_s: float) -> dict:
    evidence = state.get("evidence", [])
    return {
        "question_id": item["question_id"],
        "split": item["split"],
        "status": state.get("status"),
        "escalation_kind": state.get("escalation_kind"),
        "escalation_reason": state.get("escalation_reason"),
        "evidence_sufficient": state.get("evidence_sufficient"),
        "retrieval_attempts": state.get("retrieval_attempts", 0),
        "queries_tried": state.get("queries_tried", []),
        # all None/empty when validate did not run; `validation` describes the last draft checked
        "validation": [asdict(issue) for issue in state.get("validation", [])],
        "validation_error": state.get("validation_error"),
        "revise_attempts": state.get("revise_attempts", 0),
        "revisions": [{"draft": r["draft"].model_dump(), "issues": [asdict(issue) for issue in r["issues"]]}
                      for r in state.get("revisions", [])],
        "decision_after_validate": state.get("decision_after_validate"),
        "classification": state["classification"].model_dump() if state.get("classification") else None,
        "draft": state["draft"].model_dump() if state.get("draft") else None,
        "final_answer": state.get("final_answer"),  # None when escalated: nothing was shown to the asker
        "evidence": [e.model_dump(exclude={"text"}) for e in evidence],
        "evidence_text": {e.id: e.text for e in evidence},
        "trace": state["trace"],
        **summarize(state, wall_s),
        "error": None if state.get("draft") else f"no draft (status={state.get('status')})",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_run_args(parser)
    parser.add_argument("--grade-evidence", action="store_true", help="enable grade_evidence + rewrite_query")
    parser.add_argument("--escalation-rule", choices=sorted(RULES), help="enable escalate with this rule")
    parser.add_argument("--validate-revise", action="store_true", help="enable validate + revise")
    args = parser.parse_args()

    features = Features(grade_evidence=args.grade_evidence, escalation_rule=args.escalation_rule,
                        validate_revise=args.validate_revise)
    command_packages = load_command_packages(PROCESSED_DIR / "command_packages.json")
    command_lookup = CommandPackageLookup(command_packages)
    context = AgentContext(retriever=Retriever(), command_lookup=command_lookup)
    out_path = prepare_run(args, {
        "system": features.version, "features": asdict(features), "generator": GENERATOR_MODEL,
        "embed": EMBED_MODEL, "docs_k": 5, "history_k": 3, "max_steps": MAX_STEPS,
        "command_packages": {release: len(commands) for release, commands in command_packages.items()},
        "context": {f.name: getattr(context, f.name) for f in fields(context) if f.name != "retriever"
                    and f.name != "command_lookup"},
        "prompts": {k: v for k, v in vars(prompts).items() if k.isupper()},
    })
    graph = build_graph(features)

    def work(item: dict) -> dict:
        start = time.perf_counter()
        state = run_ticket(graph, f"{item['title']}\n\n{item['question_text']}", context)
        return to_record(item, state, time.perf_counter() - start)

    items = load_benchmark(PROCESSED_DIR / "benchmark.jsonl", args.split)
    run_jobs(items[: args.limit] if args.limit else items, work, out_path, args.budget_usd, args.workers,
             describe=lambda r: (f"{r['status']}, rounds={r['retrieval_attempts']}, "
                                 f"revisions={r['revise_attempts']}, tools={r['usage']['tool_calls']}"))


if __name__ == "__main__":
    main()

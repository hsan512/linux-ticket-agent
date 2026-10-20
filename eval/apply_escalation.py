"""Escalation rules on saved agent runs, without new LLM calls.

  select: score every rule on a (dev) run and pick one with the pre-registered criterion
          (eval/results/findings.md, item 15): highest accuracy on answered tickets with
          coverage >= --min-coverage; ties go to higher coverage.
  apply:  replay a rule over a run's predictions into a new run directory. The escalate node is a
          pure function of the draft's confidence and the grader's verdict, so the replay makes
          the same decision the graph would; judgments are copied because drafts are unchanged.

  python -m eval.apply_escalation select --run-id agent-v2-dev
  python -m eval.apply_escalation apply --source agent-v2-test --rule RULE --run-id agent-v3-test
"""

from __future__ import annotations

import argparse
import json
import shutil

from agent.config import RUNS_DIR
from agent.escalation import RULES, escalation_reason, should_escalate
from eval.common import latest_by_question, load_jsonl


def insufficient(pred: dict) -> bool:
    if pred.get("evidence_sufficient") is not None:
        return pred["evidence_sufficient"] is False
    grades = [t for t in pred["trace"] if t["node"] == "grade_evidence"]
    return bool(grades) and grades[-1].get("sufficient") is False


def escalated(pred: dict, rule: str) -> bool:
    return should_escalate(rule, pred["draft"]["confidence"], insufficient(pred))


def selective_metrics(preds: dict[int, dict], judged: dict[int, dict], rule: str) -> dict:
    qs = [q for q in judged if q in preds]
    answered = [q for q in qs if not escalated(preds[q], rule)]
    correct = sum(judged[q]["correct"] for q in answered)
    return {
        "rule": rule,
        "n": len(qs),
        "coverage": len(answered) / len(qs),
        "accuracy_on_answered": correct / len(answered) if answered else 0.0,
        "wrong_answers_shown": len(answered) - correct,
        "correct_answers_escalated": sum(judged[q]["correct"] for q in qs if q not in answered),
    }


def select_rule(preds: dict[int, dict], judged: dict[int, dict], min_coverage: float) -> tuple[str, list[dict]]:
    table = [selective_metrics(preds, judged, r) for r in RULES]
    eligible = [m for m in table if m["coverage"] >= min_coverage]
    best = max(eligible, key=lambda m: (m["accuracy_on_answered"], m["coverage"]))
    return best["rule"], table


def load_run(run_id: str) -> tuple[dict[int, dict], dict[int, dict]]:
    preds = {q: p for q, p in latest_by_question(load_jsonl(RUNS_DIR / run_id / "predictions.jsonl")).items()
             if p["error"] is None}
    judged = {q: j for q, j in latest_by_question(load_jsonl(RUNS_DIR / run_id / "judgments.jsonl")).items()
              if j["error"] is None}
    return preds, judged


def apply_rule(source: str, rule: str, run_id: str) -> None:
    src, dst = RUNS_DIR / source, RUNS_DIR / run_id
    dst.mkdir(parents=True, exist_ok=True)
    config = json.loads((src / "config.json").read_text())
    config |= {"system": f"{config['system']}+escalate_{rule}", "escalation_rule": rule,
               "replayed_from": source, "note": "offline replay of the escalate rule; no new LLM calls"}
    (dst / "config.json").write_text(json.dumps(config, indent=2))
    with (dst / "predictions.jsonl").open("w", encoding="utf-8") as f:
        for pred in latest_by_question(load_jsonl(src / "predictions.jsonl")).values():
            if pred["error"] is None and escalated(pred, rule):
                pred = pred | {"status": "escalated",
                               "escalation_reason": escalation_reason(pred["draft"]["confidence"], insufficient(pred))}
            f.write(json.dumps(pred, ensure_ascii=False) + "\n")
    shutil.copy(src / "judgments.jsonl", dst / "judgments.jsonl")
    print(f"wrote {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--run-id", required=True)
    s.add_argument("--min-coverage", type=float, default=0.5)
    a = sub.add_parser("apply")
    a.add_argument("--source", required=True)
    a.add_argument("--rule", choices=sorted(RULES), required=True)
    a.add_argument("--run-id", required=True)
    args = parser.parse_args()

    if args.cmd == "select":
        rule, table = select_rule(*load_run(args.run_id), args.min_coverage)
        for m in table:
            print(f"{m['rule']:<26} coverage {m['coverage']:6.1%}  acc-on-answered {m['accuracy_on_answered']:6.1%}"
                  f"  wrong shown {m['wrong_answers_shown']:3d}  correct escalated {m['correct_answers_escalated']:3d}")
        print(f"selected: {rule}")
    else:
        apply_rule(args.source, args.rule, args.run_id)


if __name__ == "__main__":
    main()

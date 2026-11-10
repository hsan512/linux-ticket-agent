import random

import pytest

from agent.config import cost_usd
from eval.apply_escalation import insufficient, select_rule
from eval.compare import mcnemar_exact, paired
from eval.judge import cited_ids
from eval.make_benchmark import primary_tag, proportional_allocation, stratified_sample
from eval.report import cohen_kappa, cost_metrics, wilson_ci
from eval.report import selective_metrics as report_selective


def test_proportional_allocation_sums_to_total() -> None:
    alloc = proportional_allocation({"a": 50, "b": 30, "c": 20}, 7)
    assert sum(alloc.values()) == 7
    assert alloc["a"] >= alloc["b"] >= alloc["c"]


def test_primary_tag_falls_back_to_other() -> None:
    assert primary_tag(["zfs", "bash"], {"bash"}) == "bash"
    assert primary_tag(["zfs"], {"bash"}) == "other"


def test_stratified_sample_is_deterministic_and_disjoint() -> None:
    pool = [
        {"question_id": i, "tags": ["bash" if i % 3 else "sed"]} for i in range(300)
    ]
    dev1, test1 = stratified_sample([dict(r) for r in pool], 30, 6, random.Random(1))
    dev2, test2 = stratified_sample([dict(r) for r in pool], 30, 6, random.Random(1))
    assert [r["question_id"] for r in dev1 + test1] == [
        r["question_id"] for r in dev2 + test2
    ]
    assert len(dev1) == 6 and len(test1) == 24
    assert not {r["question_id"] for r in dev1} & {r["question_id"] for r in test1}


def test_wilson_ci_contains_point_estimate() -> None:
    lo, hi = wilson_ci(60, 100)
    assert lo < 0.6 < hi
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_cohen_kappa() -> None:
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == pytest.approx(0.0)


def test_cited_ids_merges_inline_and_list() -> None:
    pred = {
        "draft": {
            "citations": ["D1", "D9"],
            "answer": "Use rsync (H2), see D1 and P1.",
        },
        "evidence_text": {"D1": "a", "H2": "b", "P1": "c"},
    }
    assert cited_ids(pred) == ["D1", "H2", "P1"]  # D9 was never retrieved


def test_mcnemar_exact() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(10, 0) == pytest.approx(2 / 2**10)
    assert mcnemar_exact(12, 3) < 0.05 < mcnemar_exact(8, 4)


def test_paired_counts_wins_and_losses() -> None:
    ref = {1: True, 2: False, 3: False, 4: True}
    other = {1: True, 2: True, 3: True, 4: False, 5: True}  # q5 not in ref: ignored
    assert paired(ref, other) | {"p_value": None} == {
        "n": 4,
        "wins": 2,
        "losses": 1,
        "p_value": None,
    }


def test_cost_metrics_per_correct() -> None:
    preds = [{"cost_usd": 0.01}, {"cost_usd": 0.03}]
    m = cost_metrics(preds, [{"cost_usd": 0.5}], n_correct=1)
    assert m["per_correct"] == pytest.approx(0.04)
    assert m["per_ticket"]["max"] == 0.03
    assert cost_metrics(preds, [], n_correct=0)["per_correct"] is None


def _pred(confidence: str, sufficient: bool | None) -> dict:
    return {
        "draft": {"confidence": confidence},
        "evidence_sufficient": sufficient,
        "trace": [],
        "error": None,
    }


def test_select_rule_respects_min_coverage_then_accuracy() -> None:
    preds = {
        1: _pred("high", True),
        2: _pred("high", True),
        3: _pred("medium", True),
        4: _pred("high", False),
    }
    judged = {
        1: {"correct": True},
        2: {"correct": True},
        3: {"correct": False},
        4: {"correct": False},
    }
    rule, table = select_rule(preds, judged, min_coverage=0.5)
    assert (
        rule == "not_high_or_insufficient"
    )  # answers q1, q2 only: 100% at 50% coverage
    rule, _ = select_rule(preds, judged, min_coverage=0.75)
    assert rule in {"confidence_not_high", "insufficient"}  # 3 answered, 2 correct
    m = {r["rule"]: r for r in table}["confidence_low"]
    assert m["coverage"] == 1.0 and m["wrong_answers_shown"] == 2


def test_insufficient_falls_back_to_trace() -> None:
    p = {
        "draft": {"confidence": "high"},
        "trace": [
            {"node": "grade_evidence", "sufficient": True},
            {"node": "grade_evidence", "sufficient": False},
        ],
    }
    assert insufficient(p)
    assert not insufficient({"draft": {}, "trace": []})


def test_report_selective_metrics() -> None:
    preds = {
        1: {"status": "answered"},
        2: {"status": "escalated"},
        3: {"status": "answered"},
    }
    judged = {1: {"correct": True}, 2: {"correct": True}, 3: {"correct": False}}
    m = report_selective(preds, judged)
    assert m["coverage"] == pytest.approx(2 / 3) and m["accuracy_on_answered"] == 0.5
    assert m["wrong_answers_shown"] == 1 and m["correct_answers_escalated"] == 1


def test_cost_usd() -> None:
    assert cost_usd("gpt-5.4-mini", 1_000_000, 1_000_000) == pytest.approx(5.25)

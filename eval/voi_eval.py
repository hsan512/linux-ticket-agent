"""Value of information: would asking the asker help? A feasibility check for a
conversational agent, run before building one (findings item 34).

Questions outside the benchmark whose asker added code or command output to the
question after posting, before the accepted answer. For each:
  2. the one-shot agent answers the FIRST version and the version just before the
     accepted answer (with the added facts); the judge scores both against the accepted
     answer, always seeing the full final question. The gap is the most that asking
     could gain.
  3. one LLM call reads the first version and lists what it would ask the asker
     (read-only commands or questions); a second call checks each ask against what the
     asker really added later. Hit rate: asks the later information answers. Coverage:
     tickets where the asks would have drawn out that information.

Stack Exchange text is CC BY-SA; the sample stays in eval/runs/ (not committed).

Usage (the history file is extracted from the dump's 7z, see build):
  python eval/voi_eval.py build --post-history PATH/PostHistory.xml --n 50
  python eval/voi_eval.py run     # agent on both versions + judge
  python eval/voi_eval.py asks    # step 3
  python eval/voi_eval.py report
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

from agent.config import GENERATOR_MODEL, PROCESSED_DIR, RUNS_DIR  # noqa: E402

OUT = RUNS_DIR / "voi"
POSTS = PROCESSED_DIR.parent / "raw" / "Posts.xml"
CODE = re.compile(r"^( {4}|\t)|```|`[^`]+`|^\$ |^# ", re.M)
MIN_ADDED = 80


def rows(path: Path):
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag == "row":
            yield el.attrib
            el.clear()


# --- build: first version, version before the answer, the accepted answer ----------


def build(args: argparse.Namespace) -> None:
    import difflib

    sys.path.insert(0, str(PROCESSED_DIR.parent))
    from se_dump import html_to_text

    bench = {
        json.loads(line)["question_id"]
        for line in (PROCESSED_DIR / "benchmark.jsonl").open()
    }
    questions, answers = {}, {}
    for r in rows(POSTS):
        if (
            r.get("PostTypeId") == "1"
            and r.get("CreationDate", "") >= "2023-01-01"
            and r.get("AcceptedAnswerId")
            and r.get("OwnerUserId")
            and int(r["Id"]) not in bench
        ):
            questions[int(r["Id"])] = {
                "title": html_to_text(r.get("Title", "")),
                "owner": r["OwnerUserId"],
                "accepted": int(r["AcceptedAnswerId"]),
                "url": f"https://unix.stackexchange.com/q/{r['Id']}",
            }
        elif r.get("PostTypeId") == "2":
            answers[int(r["Id"])] = (r["CreationDate"], r.get("Body", ""))
    for q in questions.values():
        q["answered"], q["answer_html"] = answers.get(q["accepted"], ("9999", ""))

    first, latest, owner_added = {}, {}, set()
    for r in rows(Path(args.post_history)):
        pid = int(r.get("PostId", 0))
        q = questions.get(pid)
        if not q or r.get("CreationDate", "") >= q["answered"]:
            continue
        kind = r.get("PostHistoryTypeId")
        if kind == "2":
            first[pid] = latest[pid] = r.get("Text", "")
        elif kind == "5":
            latest[pid] = r.get("Text", "")  # rows come in time order
            if r.get("UserId") == q["owner"]:
                owner_added.add(pid)

    sample = []
    for pid in sorted(owner_added):
        if pid not in first:
            continue
        diff = difflib.ndiff(first[pid].splitlines(), latest[pid].splitlines())
        added = "\n".join(line[2:] for line in diff if line.startswith("+ "))
        if len(added) >= MIN_ADDED and CODE.search(added) and len(first[pid]) >= 100:
            q = questions[pid]
            sample.append(
                {
                    "question_id": pid,
                    "url": q["url"],
                    "title": q["title"],
                    "first": first[pid],
                    "final": latest[pid],
                    "added": added,
                    "answer_text": html_to_text(q["answer_html"]),
                }
            )
    print(f"{len(sample)} questions qualify; sampling {args.n} (seed {args.seed})")
    random.Random(args.seed).shuffle(sample)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "sample.jsonl").open("w") as f:
        for item in sample[: args.n]:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load(name: str) -> list[dict]:
    path = OUT / name
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def append(name: str, record: dict) -> None:
    with (OUT / name).open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- step 2: the one-shot agent on both versions, judged the same way --------------


def run(args: argparse.Namespace) -> None:
    import threading

    from agent.graph import Features, build_graph, run_ticket
    from agent.state import AgentContext
    from agent.tools.packages import CommandPackageLookup, load_command_packages
    from agent.tools.retrieval import Retriever
    from agent.trace import summarize
    from eval.judge import judge_correctness

    features = Features(
        grade_evidence=True, validate_revise=True, escalation_rule="confidence_not_high"
    )
    lookup = CommandPackageLookup(
        load_command_packages(PROCESSED_DIR / "command_packages.json")
    )
    context = AgentContext(retriever=Retriever(), command_lookup=lookup)
    graph = build_graph(features)
    done = {(r["question_id"], r["version"]) for r in load("results.jsonl")}
    lock = threading.Lock()

    def one(job: tuple[dict, str]) -> None:
        item, version = job
        state = run_ticket(graph, f"{item['title']}\n\n{item[version]}", context)
        summary = summarize(state, 0.0)
        # the judge always reads the full final question, for both versions
        verdict, judge_cost = judge_correctness(
            {
                "title": item["title"],
                "question_text": item["final"],
                "answer_text": item["answer_text"],
            },
            {"draft": {"answer": state["draft"].answer}},
        )
        with lock:
            append(
                "results.jsonl",
                {
                    "question_id": item["question_id"],
                    "version": version,
                    "status": state.get("status"),
                    "score": verdict.score,
                    "correct": verdict.score >= 4,
                    "agent_cost": summary["cost_usd"],
                    "judge_cost": judge_cost,
                },
            )
            print(f"{item['question_id']} {version}: {verdict.score}", flush=True)

    jobs = [
        (item, version)
        for item in load("sample.jsonl")
        for version in ("first", "final")
        if (item["question_id"], version) not in done
    ]
    print(f"{len(jobs)} agent runs to do")
    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, jobs))


# --- step 3: what would the model ask, and does it match what the asker added? -----

ASK_PROMPT = """You are a Linux/Unix support engineer reading the first message of a \
ticket. Before answering, what would you ask the asker to run or tell you? Ask only \
for what changes the answer: read-only commands that show the system's state (never \
commands that change anything), or short factual questions. At most four. If the \
message already has what you need, ask nothing and set ready_to_answer."""

MATCH_PROMPT = """You compare a support engineer's requests with information the \
asker later added to their question on their own. For each request, decide if the \
later information answers it (the output of that command or an equivalent one, or the \
fact asked for). Then decide if the requests, taken together, would have drawn out the \
main point of the later information."""


class Ask(BaseModel):
    kind: Literal["command", "question"]
    text: str = Field(description="The command to run, or the question.")
    why: str = Field(description="What it would tell you, in a few words.")


class AskPlan(BaseModel):
    asks: list[Ask]
    ready_to_answer: bool


class AskMatch(BaseModel):
    answered: list[bool] = Field(description="One per request, in order.")
    main_point_requested: bool


def asks(args: argparse.Namespace) -> None:
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.llm import invoke_structured
    from agent.tools.danger_level import find_dangers

    done = {r["question_id"] for r in load("asks.jsonl")}

    def one(item: dict) -> None:
        plan = invoke_structured(
            GENERATOR_MODEL,
            AskPlan,
            [
                SystemMessage(ASK_PROMPT),
                HumanMessage(f"{item['title']}\n\n{item['first']}"),
            ],
        )
        requests = plan.parsed.asks[:4]
        cost = plan.cost_usd
        answered, main_point = [], False
        if requests:
            listing = "\n".join(
                f"{i}. ({a.kind}) {a.text}" for i, a in enumerate(requests, 1)
            )
            match = invoke_structured(
                GENERATOR_MODEL,
                AskMatch,
                [
                    SystemMessage(MATCH_PROMPT),
                    HumanMessage(
                        f"Requests:\n{listing}\n\n---\nInformation the asker added later:\n{item['added'][:4000]}"  # noqa: E501
                    ),
                ],
            )
            answered = match.parsed.answered[: len(requests)]
            main_point = match.parsed.main_point_requested
            cost += match.cost_usd
        unsafe = [
            a.text for a in requests if a.kind == "command" and find_dangers(a.text)
        ]
        append(
            "asks.jsonl",
            {
                "question_id": item["question_id"],
                "asks": [a.model_dump() for a in requests],
                "ready_to_answer": plan.parsed.ready_to_answer,
                "answered": answered,
                "main_point_requested": main_point,
                "flagged_commands": unsafe,
                "cost_usd": cost,
            },
        )

    todo = [i for i in load("sample.jsonl") if i["question_id"] not in done]
    print(f"{len(todo)} tickets to ask about")
    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, todo))


# --- report --------------------------------------------------------------------------


def report(args: argparse.Namespace) -> None:
    results = {(r["question_id"], r["version"]): r for r in load("results.jsonl")}
    ids = sorted(
        {q for q, _ in results if (q, "first") in results and (q, "final") in results}
    )
    first = [results[(q, "first")] for q in ids]
    final = [results[(q, "final")] for q in ids]
    n = len(ids)
    acc = lambda rs: sum(r["correct"] for r in rs) / n  # noqa: E731
    mean = lambda rs: sum(r["score"] for r in rs) / n  # noqa: E731
    up = sum(b["correct"] and not a["correct"] for a, b in zip(first, final))
    down = sum(a["correct"] and not b["correct"] for a, b in zip(first, final))
    spent = sum(r["agent_cost"] + r["judge_cost"] for r in results.values())
    print(f"## Step 2: value of the added information ({n} questions)\n")
    print("| version the agent saw | correct (score >= 4) | mean score | answered |")
    print("|---|---|---|---|")
    for name, rs in (("first message", first), ("with the asker's additions", final)):
        answered = sum(r["status"] == "answered" for r in rs) / n
        print(f"| {name} | {acc(rs):.0%} | {mean(rs):.2f} | {answered:.0%} |")
    print(
        f"\nper question: {up} became correct with the additions, {down} became wrong (McNemar-style counts); spent ${spent:.2f}"  # noqa: E501
    )
    a = load("asks.jsonl")
    if a:
        with_asks = [r for r in a if r["asks"]]
        n_asks = sum(len(r["asks"]) for r in a)
        hits = sum(sum(r["answered"]) for r in a)
        print(f"\n## Step 3: what the model would ask ({len(a)} first messages)\n")
        print(
            f"- asked something: {len(with_asks)}/{len(a)}; asks per ticket {n_asks / len(a):.1f}; said ready to answer: {sum(r['ready_to_answer'] for r in a)}"  # noqa: E501
        )
        print(
            f"- hit rate (asks the later information answers): {hits}/{n_asks} = {hits / max(n_asks, 1):.0%}"  # noqa: E501
        )
        print(
            f"- coverage (asks would have drawn out the main later point): {sum(r['main_point_requested'] for r in a)}/{len(a)}"  # noqa: E501
        )
        flagged = [c for r in a for c in r["flagged_commands"]]
        commands = sum(1 for r in a for x in r["asks"] if x["kind"] == "command")
        print(
            f"- requested commands flagged by the danger check: {len(flagged)}/{commands} {flagged[:3]}"  # noqa: E501
        )
        print(f"- spent ${sum(r['cost_usd'] for r in a):.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--post-history", required=True)
    b.add_argument("--n", type=int, default=50)
    b.add_argument("--seed", type=int, default=0)
    for name in ("run", "asks"):
        sub.add_parser(name).add_argument("--workers", type=int, default=4)
    sub.add_parser("report")
    args = parser.parse_args()
    {"build": build, "run": run, "asks": asks, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()

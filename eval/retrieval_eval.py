"""Retrieval-only score: do the retrieved passages contain what the accepted answer
needed? Runs without the agent, the LLM, or the judge, so retrieval variants (k,
chunking, hybrid search, ...) can be compared for a fraction of a cent.

For each dev ticket:
  1. answer terms: commands, options, paths, and dotted config keys from the <code>
     spans of the accepted answer, minus common words and minus anything already in the
     question (the asker knew those, so finding them proves nothing);
  2. retrieval: the ticket (title + question) as the query, top-k from docs and from
     history, for several k;
  3. recall@k: the share of answer terms found in the passages; hit@k: at least one.

Limits: a proxy (a right answer can use other commands than the accepted one) on 40
tickets, so it ranks variants; it does not measure effect sizes. Decisions on dev only;
the test split is not read.

Usage:
  python eval/retrieval_eval.py --variant baseline
  python eval/retrieval_eval.py --variant sections --docs-collection docs_sections
  python eval/retrieval_eval.py --variant steps --query-mode steps
  python eval/retrieval_eval.py --variant twohop --query-mode twohop
  python eval/retrieval_eval.py --variant plan-twohop --query-mode plan+twohop
  python eval/retrieval_eval.py --compare baseline sections
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

from agent.config import (  # noqa: E402
    DOCS_COLLECTION,
    EMBED_MODEL,
    HISTORY_COLLECTION,
    PROCESSED_DIR,
    RUNS_DIR,
    cost_usd,
)

KS = (3, 5, 10, 20)
MAX_K = max(KS)
# too common in shell answers to show that retrieval found anything specific
STOP = frozenset(
    {
        "sudo",
        "cd",
        "ls",
        "if",
        "fi",
        "do",
        "in",
        "sh",
        "echo",
        "cat",
        "grep",
        "bash",
        "printf",
        "then",
        "else",
        "elif",
        "done",
        "for",
        "while",
        "case",
        "esac",
        "exit",
        "true",
        "false",
        "test",
        "export",
        "local",
        "return",
        "read",
        "set",
        "the",
        "and",
        "not",
        "you",
        "your",
        "file",
        "files",
        "dir",
        "path",
        "name",
        "user",
        "home",
        "tmp",
        "foo",
        "bar",
        "baz",
        "root",
        "example",
        "/dev/null",
        "/tmp",
        "/home",
        "/etc",
        "/usr",
        "/bin",
        "/var",
    }
)
COMMAND_STARTERS = frozenset(
    {"|", "||", "&&", ";", "sudo", "xargs", "exec", "env", "$("}
)
OPTION = re.compile(r"--?[A-Za-z][\w-]*")
COMMAND = re.compile(r"[a-z][a-z0-9_.+-]*[a-z0-9]")
DOTTED_KEY = re.compile(r"([A-Za-z_][\w-]*(?:\.[\w-]+)+)=.*")
PATH = re.compile(r"(?:/|~/)[\w.@%+-]+(?:/[\w.@%+-]+)*/?")
TRIM = "\"'`,;:()[]{}<>"


def code_spans(answer_html: str) -> list[str]:
    """The text inside every <code> element, HTML entities decoded."""
    return [
        html.unescape(span)
        for span in re.findall(r"<code[^>]*>(.*?)</code>", answer_html, re.S)
    ]


def span_terms(span: str) -> set[str]:
    """Candidate terms in one code span (before the stop list and question filter)."""
    terms: set[str] = set()
    for line in span.splitlines():
        tokens = line.split()
        command_position = True  # the first word of a line is a command
        for raw in tokens:
            if raw in COMMAND_STARTERS:
                command_position = True
                continue
            token = raw.strip(TRIM)
            if "$" in token or not token:
                command_position = False
                continue
            option = OPTION.fullmatch(token.split("=")[0])
            key = DOTTED_KEY.fullmatch(token)
            if option:
                terms.add(option.group(0))
            elif key:
                terms.add(key.group(1))
            elif PATH.fullmatch(token):
                terms.add(token.rstrip("/") or token)
            elif command_position and COMMAND.fullmatch(token):
                terms.add(token)
            command_position = False
    return terms


def found(term: str, text: str) -> bool:
    """Exact, case-sensitive token match: -l does not match inside -ltnp, ss not in
    class, but tun-mtu does match --tun-mtu."""
    # options keep a dash boundary on the left (-l is not in --l); plain words may
    # follow dashes (tun-mtu in a config file is --tun-mtu in the man page)
    left = r"(?<![\w-])" if term.startswith("-") else r"(?<!\w)"
    return re.search(rf"{left}{re.escape(term)}(?![\w-])", text) is not None


def answer_terms(ticket: dict) -> list[str]:
    """What the accepted answer needed that the question did not already say."""
    question = f"{ticket['title']}\n{ticket['question_text']}"
    candidates: set[str] = set()
    for span in code_spans(ticket["answer_html"]):
        candidates |= span_terms(span)
    return sorted(
        t
        for t in candidates
        # two-letter commands count (ss, ip, df, ps); two-character options do not (-l)
        if (len(t) > 2 or (len(t) == 2 and not t.startswith("-")))
        and t not in STOP
        and not t.isdigit()
        and not found(t, question)
    )


def score(terms: list[str], passages: list[str], ks: tuple[int, ...] = KS) -> dict:
    """recall@k and hit@k of `terms` in the first k passages, for each k."""
    out = {}
    for k in ks:
        text = "\n".join(passages[:k])
        hits = [t for t in terms if found(t, text)]
        out[k] = {
            "recall": len(hits) / len(terms),
            "hit": int(bool(hits)),
            "found": hits,
        }
    return out


# --- query mode "steps": a static plan made before any search -------------------
# To reach the outcome C, the asker usually needs intermediate steps A and B whose
# tools the question never names; a search for C's wording misses them. The plan is
# made once, up front (no adapting to what is found); docs are searched per step and
# history with the ticket as before (past questions match on the symptom).

STEPS_PROMPT = """You plan documentation searches for a Linux/Unix support ticket.
Break the solution into the steps someone would take to reach the outcome: to get C, \
which A and B must happen first (find something out, check a setting, change a \
config, run a command). A simple ticket may need only one step; use at most four.
For each step, write one search query for man pages and GNU manuals: describe the \
behaviour or setting needed in documentation language, not the asker's symptoms. Name \
the command, option, or config file if you know it."""


class PlanStep(BaseModel):
    goal: str = Field(description="What this step achieves, in a few words.")
    docs_query: str = Field(description="Search query for man pages and manuals.")


class SearchPlan(BaseModel):
    steps: list[PlanStep] = Field(description="One to four steps, in order.")


def plan_steps(ticket_text: str) -> tuple[list[PlanStep], float]:
    """The static plan for one ticket, and its cost in USD."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.config import GENERATOR_MODEL
    from agent.llm import invoke_structured

    result = invoke_structured(
        GENERATOR_MODEL,
        SearchPlan,
        [SystemMessage(STEPS_PROMPT), HumanMessage(f"Ticket:\n{ticket_text}")],
    )
    return result.parsed.steps[:4], result.cost_usd


def round_robin(lists: list[list]) -> list:
    """Interleave ranked lists (1st of each, then 2nd of each, ...), skipping
    duplicates, so the first k items share one budget of k passages across steps."""
    merged, seen = [], set()
    for rank in range(max((len(x) for x in lists), default=0)):
        for ranked in lists:
            if rank < len(ranked):
                item = ranked[rank]
                key = (item.title, item.text[:200])
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
    return merged


# --- query mode "twohop": history first, then the docs pages of what it used -------
# Past answers name the tools a plan cannot foresee. The second hop reads the commands
# at the start of lines in the top past answers (their code, in plain text), keeps
# those with a page in the docs corpus, and searches each page for the chunk closest
# to the ticket. No LLM: a lower bound for a model choosing the hop.

HOP_FROM = 5  # past answers the second hop reads
HOP_COMMANDS = 4  # pages it looks up at most


def page_titles() -> dict[str, list[str]]:
    """Command name -> its docs titles: ls(1) man page, GNU coreutils: ls invocation."""
    titles: dict[str, list[str]] = {}
    with (PROCESSED_DIR / "docs.jsonl").open() as f:
        for line in f:
            title = json.loads(line)["title"]
            m = re.fullmatch(r"(.+?)\(\w+\) man page", title) or re.fullmatch(
                r"GNU \w+ manual: (\S+) invocation", title
            )
            if m:
                titles.setdefault(m.group(1), []).append(title)
    return titles


def hop_commands(answers: list[str], titles: dict[str, list[str]]) -> list[str]:
    """Commands the past answers run that have a docs page, in order of appearance."""
    commands: list[str] = []
    for answer in answers:
        for line in answer.splitlines():
            for term in sorted(span_terms(line)):
                if term in titles and term not in STOP and term not in commands:
                    commands.append(term)
    return commands[:HOP_COMMANDS]


def page_search(collection: str, page_titles_: list[str], query: str, k: int) -> list:
    """The k chunks of the given pages closest to `query`."""
    from llama_index.core import VectorStoreIndex
    from llama_index.core.vector_stores import (
        FilterOperator,
        MetadataFilter,
        MetadataFilters,
    )
    from qdrant_client import QdrantClient

    from agent.config import QDRANT_URL
    from agent.tools.retrieval import _doc_evidence, embed_model, vector_store

    index = VectorStoreIndex.from_vector_store(
        vector_store(QdrantClient(url=QDRANT_URL), collection),
        embed_model=embed_model(),
    )
    only = MetadataFilters(
        filters=[
            MetadataFilter(key="title", value=page_titles_, operator=FilterOperator.IN)
        ]
    )
    hits = index.as_retriever(similarity_top_k=k, filters=only).retrieve(query)
    return [_doc_evidence(h, i) for i, h in enumerate(hits, start=1)]


def saved_plans(variant: str) -> dict[int, list[PlanStep]]:
    """Plans from an earlier steps run, so plan+twohop costs no new LLM calls."""
    path = RUNS_DIR / f"retrieval-{variant}-dev" / "per_ticket.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return {r["question_id"]: [PlanStep(**s) for s in r["plan"]] for r in rows}


def run(args: argparse.Namespace) -> None:
    import tiktoken

    from agent.tools.retrieval import Retriever
    from eval.common import load_benchmark

    tickets = load_benchmark(PROCESSED_DIR / "benchmark.jsonl", "dev")
    queries = [f"{t['title']}\n\n{t['question_text']}" for t in tickets]
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = 2 * sum(len(enc.encode(q, disallowed_special=())) for q in queries)
    print(
        f"{len(tickets)} dev tickets, query mode {args.query_mode}: about {tokens} "
        f"query tokens = ${cost_usd(EMBED_MODEL, tokens):.4f} ({EMBED_MODEL})"
        + (", plus one planning call per ticket" if args.query_mode == "steps" else "")
    )
    plan_cost = 0.0
    titles = page_titles()
    plans = saved_plans(args.plans_from) if args.query_mode == "plan+twohop" else {}
    retriever = Retriever(
        docs_k=MAX_K,
        history_k=MAX_K,
        docs_collection=args.docs_collection,
        history_collection=args.history_collection,
    )
    out_dir = RUNS_DIR / f"retrieval-{args.variant}-dev"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for ticket, query in zip(tickets, queries):
        terms = answer_terms(ticket)
        plan = None
        history = retriever.search_similar(query)
        hops: list[str] = []
        if args.query_mode in ("twohop", "plan+twohop"):
            hops = hop_commands([e.text for e in history[:HOP_FROM]], titles)
            hop_lists = [
                page_search(args.docs_collection, titles[c], query, MAX_K) for c in hops
            ]
        if args.query_mode == "steps":
            plan, cost = plan_steps(query)
            plan_cost += cost
            # one ranked list per step, merged so that docs@k is still k passages
            docs = round_robin([retriever.search_docs(s.docs_query) for s in plan])
        elif args.query_mode == "twohop":
            docs = round_robin([*hop_lists, retriever.search_docs(query)])
        elif args.query_mode == "plan+twohop":
            plan = plans[ticket["question_id"]]
            step_lists = [retriever.search_docs(s.docs_query) for s in plan]
            docs = round_robin([*step_lists, *hop_lists])
        else:
            docs = retriever.search_docs(query)
        row = {
            "question_id": ticket["question_id"],
            "terms": terms,
            "plan": [s.model_dump() for s in plan] if plan else None,
            "hop_commands": hops,
            "docs": [f"{e.title} ({e.score:.3f})" for e in docs[:MAX_K]],
            "history": [f"{e.title} ({e.score:.3f})" for e in history],
        }
        if terms:
            row["score"] = {
                "docs": score(terms, [e.text for e in docs]),
                "history": score(terms, [e.text for e in history]),
                # both: k from each collection together, as the agent gets them
                "both": {
                    k: score(terms, [e.text for e in docs[:k] + history[:k]], (2 * k,))[
                        2 * k
                    ]
                    for k in KS
                },
            }
        rows.append(row)
    with (out_dir / "per_ticket.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    summary = summarize(args, rows)
    summary["query_mode"] = args.query_mode
    summary["planning_cost_usd"] = round(plan_cost, 4)
    if plan_cost:
        print(f"planning calls: ${plan_cost:.4f}")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(table([summary]))


def summarize(args: argparse.Namespace, rows: list[dict]) -> dict:
    measured = [r for r in rows if r.get("score")]
    summary = {
        "variant": args.variant,
        "docs_collection": args.docs_collection,
        "history_collection": args.history_collection,
        "tickets": len(rows),
        "measured": len(measured),
        "no_terms": len(rows) - len(measured),
        "mean_terms": sum(len(r["terms"]) for r in measured) / max(len(measured), 1),
    }
    for source in ("docs", "history", "both"):
        for k in KS:
            vals = [r["score"][source][k] for r in measured]
            summary[f"{source}_recall@{k}"] = sum(v["recall"] for v in vals) / len(vals)
            summary[f"{source}_hit@{k}"] = sum(v["hit"] for v in vals) / len(vals)
    return summary


def table(summaries: list[dict]) -> str:
    lines = []
    for source in ("docs", "history", "both"):
        lines.append(f"\n**{source}** (measured tickets; recall / hit)\n")
        lines.append("| variant | n | " + " | ".join(f"@{k}" for k in KS) + " |")
        lines.append("|---|---|" + "---|" * len(KS))
        for s in summaries:
            cells = [
                f"{s[f'{source}_recall@{k}']:.0%} / {s[f'{source}_hit@{k}']:.0%}"
                for k in KS
            ]
            lines.append(
                f"| {s['variant']} | {s['measured']} | " + " | ".join(cells) + " |"
            )
    return "\n".join(lines)


def compare(variants: list[str]) -> None:
    summaries = [
        json.loads((RUNS_DIR / f"retrieval-{v}-dev" / "summary.json").read_text())
        for v in variants
    ]
    print(table(summaries))
    if len(variants) >= 2:  # per-ticket wins / losses against the first variant

        def recalls(v: str) -> dict[int, float]:
            path = RUNS_DIR / f"retrieval-{v}-dev" / "per_ticket.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            return {
                r["question_id"]: r["score"]["both"]["5"]["recall"]
                for r in rows
                if r.get("score")
            }

        ref = recalls(variants[0])
        for v in variants[1:]:
            other = recalls(v)
            common = ref.keys() & other.keys()
            wins = sum(other[q] > ref[q] for q in common)
            losses = sum(other[q] < ref[q] for q in common)
            print(
                f"\n{v} vs {variants[0]} (both@5 recall, per ticket): "
                f"+{wins} / -{losses} of {len(common)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--docs-collection", default=DOCS_COLLECTION)
    parser.add_argument("--history-collection", default=HISTORY_COLLECTION)
    parser.add_argument(
        "--query-mode",
        choices=["ticket", "steps", "twohop", "plan+twohop"],
        default="ticket",
        help="ticket: search with the ticket text; steps: plan 1-4 steps first and "
        "search docs once per step (one LLM call per ticket); twohop: history first, "
        "then the docs pages of the commands its top answers use (no LLM); "
        "plan+twohop: both, reusing the plans saved by --plans-from (no LLM)",
    )
    parser.add_argument("--plans-from", default="steps", metavar="VARIANT")
    parser.add_argument("--compare", nargs="+", metavar="VARIANT")
    args = parser.parse_args()
    if args.compare:
        compare(args.compare)
    else:
        run(args)


if __name__ == "__main__":
    main()

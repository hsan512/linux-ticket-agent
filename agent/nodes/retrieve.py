"""retrieve: the model chooses which tools to call (function calling); we execute them.

Tools: search_docs and search_similar (vector search) and check_command (Ubuntu package
lookup). The model may call several, or nothing at all. Tool failures are retried, then
logged in the trace and skipped; they never abort the ticket.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent import prompts
from agent.config import EMBED_MODEL, GENERATOR_MODEL, cost_usd
from agent.llm import invoke_tools
from agent.nodes.common import llm_trace
from agent.schemas import CheckCommand, Evidence, SearchDocs, SearchSimilar
from agent.state import AgentContext, AgentState, TraceEntry
from agent.tools.packages import CommandPackageLookup
from agent.tools.retrieval import Retriever

PREFIXES = {
    "docs": "D",
    "history": "H",
    "package": "P",
}  # Evidence.source -> citation ID prefix


@dataclass(frozen=True)
class ToolSpec:
    """What the executor needs to know about a tool, besides the function that runs
    it."""

    log_name: str  # name in traces, reports, and queries_tried
    arg: str  # the tool's single argument, passed to its function as a string
    embeds: bool  # True if each call embeds `arg` (billed at EMBED_MODEL rates)


TOOLS = [
    SearchDocs,
    SearchSimilar,
    CheckCommand,
]  # schemas sent to the model via bind_tools
TOOL_SPECS = {  # keyed by schema class name, which is the tool name the model calls
    "SearchDocs": ToolSpec(log_name="search_docs", arg="query", embeds=True),
    "SearchSimilar": ToolSpec(log_name="search_similar", arg="query", embeds=True),
    "CheckCommand": ToolSpec(log_name="check_command", arg="command", embeds=False),
}
_enc = tiktoken.get_encoding("cl100k_base")


def _tool_fn(
    retriever: Retriever, command_lookup: CommandPackageLookup, name: str
) -> Callable[[str], list[Evidence]] | None:
    """The function behind a tool name, bound to this run's objects; None if the tool is
    unknown."""
    return {
        "SearchDocs": retriever.search_docs,
        "SearchSimilar": retriever.search_similar,
        "CheckCommand": command_lookup.lookup_command,
    }.get(name)


def run_tool(
    fn: Callable[[str], list[Evidence]], query: str, retries: int
) -> tuple[list[Evidence], str | None]:
    """Call a tool, retrying on exceptions. Returns (results, last error or None)."""
    error = None
    for _ in range(retries + 1):
        try:
            return fn(query), None
        except (
            Exception
        ) as exc:  # returned to the caller, which records it in the trace
            error = repr(exc)
    return [], error


def _execute_one(
    call: dict,
    index: int,
    retriever: Retriever,
    command_lookup: CommandPackageLookup,
    max_calls: int,
    retries: int,
) -> tuple[list[Evidence], TraceEntry]:
    """Run one requested tool call. Problems (over the cap, unknown tool, missing
    argument, tool failure) are recorded in the returned trace entry, never raised."""
    name, args = call["name"], call.get("args", {})
    spec = TOOL_SPECS.get(name)
    # unknown tools are logged under the name the model used, to show what it invented
    entry = TraceEntry(
        node="retrieve",
        tool=spec.log_name if spec else name,
        args=args,
        results=0,
        latency_s=0.0,
        cost_usd=0.0,
        error=None,
    )
    fn = _tool_fn(retriever, command_lookup, name)
    if (
        index >= max_calls
    ):  # checked first: calls over the cap are not looked at further
        entry["error"] = f"skipped: more than {max_calls} tool calls requested"
        return [], entry
    if spec is None or fn is None:
        entry["error"] = f"unknown tool {name!r}"
        return [], entry
    if spec.arg not in args:  # fail loudly rather than run the tool on an empty string
        entry["error"] = f"missing argument {spec.arg!r}"
        return [], entry
    value = str(args[spec.arg])
    start = time.perf_counter()
    found, entry["error"] = run_tool(fn, value, retries)
    entry["latency_s"] = time.perf_counter() - start
    entry["cost_usd"] = (
        cost_usd(EMBED_MODEL, len(_enc.encode(value))) if spec.embeds else 0.0
    )
    entry["results"] = len(found)
    return found, entry


def execute_tool_calls(
    tool_calls: Sequence[dict],
    retriever: Retriever,
    command_lookup: CommandPackageLookup,
    max_calls: int,
    retries: int,
) -> tuple[list[Evidence], list[TraceEntry]]:
    """Run up to `max_calls` requested tool calls concurrently; one trace entry per
    requested call, results kept in the order the model requested them."""
    if not tool_calls:
        return [], []
    with ThreadPoolExecutor(
        max_workers=max(1, min(len(tool_calls), max_calls))
    ) as pool:
        outcomes = list(
            pool.map(
                lambda ic: _execute_one(
                    ic[1], ic[0], retriever, command_lookup, max_calls, retries
                ),
                enumerate(tool_calls),
            )
        )
    results = [e for found, _ in outcomes for e in found]
    return renumber(results), [entry for _, entry in outcomes]


def renumber(evidence: list[Evidence]) -> list[Evidence]:
    """Drop duplicate passages (several queries can hit the same chunk) and number them
    D1.., H1.., P1.. in order of appearance, so citation IDs are unique within the
    ticket."""
    seen: set[tuple[str, str]] = set()
    counters = dict.fromkeys(PREFIXES, 0)
    out: list[Evidence] = []
    for e in evidence:
        key = (e.url, e.text)
        if key in seen:
            continue
        seen.add(key)
        counters[e.source] += 1
        out.append(
            e.model_copy(update={"id": f"{PREFIXES[e.source]}{counters[e.source]}"})
        )
    return out


def retrieve(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    """First round: the model plans searches. Later rounds: run the searches
    rewrite_query planned. New passages are merged with the ones grade_evidence kept
    from earlier rounds."""
    ctx = runtime.context
    trace: list[TraceEntry] = []
    tool_calls = state.get("planned_calls") or []
    if not tool_calls:
        plan = invoke_tools(
            GENERATOR_MODEL,
            TOOLS,
            [
                SystemMessage(prompts.RETRIEVE),
                HumanMessage(f"Ticket:\n{state['ticket']}"),
            ],
        )
        tool_calls = plan.message.tool_calls if plan.message else []
        trace.append(llm_trace("retrieve", plan, tool_calls_requested=len(tool_calls)))
    start = time.perf_counter()
    found, tool_trace = execute_tool_calls(
        tool_calls,
        ctx.retriever,
        ctx.command_lookup,
        ctx.max_tool_calls,
        ctx.tool_retries,
    )
    # tools overlap, so their batch wall time is less than their summed latency
    trace.append(
        TraceEntry(
            node="retrieve",
            tools_wall_s=time.perf_counter() - start,
            latency_s=0.0,
            cost_usd=0.0,
            error=None,
        )
    )
    # shown to rewrite_query as "earlier searches": only calls that ran (within the cap,
    # known tool)
    queries_tried: list[str] = []
    for c in tool_calls[: ctx.max_tool_calls]:
        if c.get("name") in TOOL_SPECS:
            spec = TOOL_SPECS[c["name"]]
            queries_tried.append(
                f"{spec.log_name}: {c.get('args', {}).get(spec.arg, '')}"
            )
    return {
        "evidence": renumber(state.get("evidence", []) + found),
        "retrieval_attempts": state.get("retrieval_attempts", 0) + 1,
        "planned_calls": [],
        "queries_tried": queries_tried,
        "trace": trace + tool_trace,
    }

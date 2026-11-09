# CLAUDE.md: Troubleshooting Agent Project

Project context for Claude Code. Read this at the start of every session, and ask before making
assumptions. Items marked TODO are undecided.

## 1. Goal

A tool-using agent that triages Linux support questions, evaluated against simpler baselines.
Every reported number comes from a measured run, and the demo claims no users it does not have.

How the work is split: the developer writes the core logic and makes the design decisions;
Claude Code explains, reviews by running code against test cases, and writes code when asked
(mostly tests, prompts, docstrings, and the Linux-heavy command analysis).

## 2. Decisions made

| Topic | Decision |
|---|---|
| Project | Troubleshooting/ticket-triage agent |
| Frameworks | LangGraph (orchestration), LangChain (LLM calls, tools, structured output), LlamaIndex (retrieval tools only) |
| LLM | OpenAI API with function calling; structured outputs via Pydantic |
| Data | Unix & Linux Stack Exchange, dump 2025-12-31 (community release on archive.org); see `data/SOURCES.md` |
| Env | Conda env `ticket-agent` (Python 3.12), `environment.yml` + pinned `requirements.txt`; no venv |
| Ground truth | Accepted answers (community-verified) |
| RAG corpus | Man pages + GNU manuals (Info) in Qdrant (docker compose); plus pre-2023 U&L Q&A as "similar issues" (time split, no leakage) |
| Benchmark | 200 questions from 2023+ (40 dev / 160 test), stratified by tag; RAG-only and LLM-only baselines |
| Models | Generator `gpt-5.4-mini`, judge `gpt-6-sol` (different family), embeddings `text-embedding-3-small` |
| Serving | FastAPI + Docker; public demo on AWS with rate limits and budget alarms (not yet deployed) |
| Real users | None guaranteed. Optional small pilot only with community/mod permission. Report real numbers only |

Rejected options and why: posting a bot on third-party GitHub repos without maintainer
consent; executing proposed commands in a sandbox (they run on the asker's machine, so a
sandbox proves little; static checks are used instead).

## 3. Legal and honesty rules

- Stack Exchange content is CC BY-SA: attribute the source (link original posts, name the site), keep derived work under a compatible license. Re-read the current licensing/terms page before downloading.
- GitHub: reading public issues via API is fine, but read the API Terms (Section H) and Acceptable Use Policies. Never post on others' repos without consent. No bulk-registered bot accounts.
- README must state: data sources, licenses, what is synthetic vs real, benchmark limitations.
- Claims only after measurement. No fake users, no "production" wording for a demo.
- LLM grading is never called human-verified.

## 4. What the agent does

Single agent with a multi-step workflow and several tools (not multi-agent; do not call it that).

Input: a ticket (title + question). Output: `answered` (answer with citations) or `escalated`
(handed to a human with a reason).

### Flow

```
classify -> retrieve -> grade_evidence
              ^             | weak evidence, rounds left -> rewrite_query -> retrieve
              |             v
              +-------- draft
                          | escalation rule fires (confidence) -> escalate
                          v
                       validate (static checks; nothing is executed)
                          | syntax / catastrophic, tries left -> revise -> validate
                          | syntax / catastrophic, no tries   -> escalate
                          | check could not run               -> escalate
                          v
                       finalize (warnings for destructive and unknown commands)
```

Features are switched on one at a time (`agent/graph.py`, `Features`) so each can be measured.

### Nodes and tools

| Node | Purpose | Implementation |
|---|---|---|
| `classify` | Issue type, risk level | LLM + Pydantic |
| `retrieve` | The model picks tools: `search_docs`, `search_similar` (LlamaIndex retrievers), `check_command` (Ubuntu package lookup) | function calling |
| `grade_evidence` | Keep relevant passages, decide if they suffice | LLM grader |
| `rewrite_query` | Plan new searches from what is missing | LLM |
| `draft` | Answer with citations and a confidence level | LLM + Pydantic |
| `validate` | `bash -n` syntax, command names against the package table, danger analysis of each command (bashlex, worst-case expansion) | no LLM |
| `revise` | Fix syntax or catastrophic issues, keep everything else | LLM + Pydantic |
| `escalate` / `finalize` | Hand off with a reason, or answer with code-added warnings | no LLM |

### Guardrails

- Caps on graph steps, retrieval rounds, tool calls per step, and revisions.
- Tool failures are retried with limits and logged in the trace, never swallowed.
- Proposed commands are never executed; they are checked statically and risky ones are warned about.
- Citations come from retrieved passages; the API shows only the passages the answer cites.
- The API adds: length limits (questions over the benchmark's 4,000 characters go to a human), a per-IP rate limit, a daily budget, and a hand-off queue for escalated tickets.

## 5. Evaluation plan

Baselines and benchmark were built before the agent, so improvements are provable.

- Baselines: LLM-only and RAG-only.
- Metrics: match with the accepted answer (LLM-as-a-Judge, not hand-graded; checking it against an independent second grader from another vendor is planned, not yet done); invalid-command rate; tool errors; citation faithfulness; escalation coverage and accuracy on answered tickets; steps, latency, and cost per ticket.
- Ablation: add `grade_evidence`/`rewrite_query`, escalation, and `validate` one at a time.
- Decisions on dev only; each final configuration runs on test once. Results and their caveats are in `eval/results/findings.md`.
- Limitation to state: the checks show that commands are valid and not dangerous, not that they fix the asker's problem.

## 6. Repo layout

```
agent/    graph.py, state.py, nodes/, tools/, schemas.py
api/      FastAPI app, hand-off queue, daily budget
data/     scripts to download and filter the dump (record source + license)
eval/     benchmark, baselines, judge, reports, findings
tests/    unit tests (no API calls)
Dockerfile, docker-compose.yml
README.md
requirements.txt   (pinned)
```

## 7. Build order

1. Data: choose the site, download and filter, record license/attribution. Done.
2. Baselines and benchmark. Done.
3. Minimal graph: `classify -> retrieve -> draft -> finalize`. Done.
4. `grade_evidence`/`rewrite_query`, escalation, `validate`/`revise`; measure after each. Done.
5. FastAPI and Docker (done); then CI, metrics and dashboards, AWS deploy with CD, agent tracing.
6. README: architecture, results, failure analysis, limitations.

## 8. Working rules for Claude Code

- LangChain and LangGraph APIs change quickly. Check current docs before writing code and pin versions; do not copy old examples.
- Ask before: spending money (OpenAI, cloud), downloading large datasets, or anything public (pushing, publishing).
- Keep functions small and typed, add unit tests for tools, and keep secrets out of the repo (environment variables only).
- Write a component's contract (what it reads, what it writes, who decides routing) and get it confirmed before the code is written.

## 9. Open TODOs

- Decide whether to run an optional small pilot and where, with permission.

# Findings log

Test split: 160 Unix & Linux questions (2023-2025). Judge `gpt-6-sol`, correct = score >= 4.
Full comparison table: `comparison-test.md`.

## 2026-10-19: baselines and minimal agent (build steps 2-3)

| System | Accuracy | Cost/ticket | Cost/correct | Latency p50 |
|---|---|---|---|---|
| llm_only (no retrieval) | 45.6% | $0.0026 | $0.0056 | 2.5s |
| rag (retrieve once, answer) | 38.8% | $0.0055 | $0.0141 | 2.9s |
| agent_v1_minimal (classify, model-chosen searches, draft) | 36.2% | $0.0073 | $0.0201 | 4.8s |

1. **Retrieval does not help on this benchmark, and may hurt.** llm_only beats rag on 26
   tickets and loses 15 (McNemar p = 0.117, not significant). In 16 tickets, llm_only is
   right and both retrieval systems are wrong. Possible causes, not yet separated:
   irrelevant passages mislead the model; or contamination, i.e. 2023-2025 questions may be
   in the model's training data, which would inflate llm_only.
2. **The minimal agent is no better than rag** (+15 / -19, p = 0.608), at 1.3x the cost and
   1.7x the latency. Letting the model write its own queries did not fix retrieval quality.
3. **Retrieval scores do not separate useful from useless evidence.** The mean top cosine
   score is about the same when the agent is right (docs 0.535) as when it is wrong but
   llm_only is right (0.536). A score threshold for `grade_evidence` will not work; it needs
   an LLM grader.
4. **The draft's self-reported confidence is informative.** Accuracy is 50% at high (88
   tickets), 21% at medium (67), and 0% at low (5). This makes it a candidate signal for
   `escalate`.
5. The model never chose to skip retrieval: every ticket made 1-6 search calls. Six calls
   were skipped by the cap of 4.
6. `missing_info` was empty for all 160 tickets, so `ask_user` would never fire on this
   benchmark, which contains only answered questions. Escalation and ask-back can't be
   measured offline here (a known limitation).
7. **Contamination check (free; reuses the judgments above).** llm_only's lead over rag comes
   almost entirely from 2023 questions:

   | Created | n | llm_only | rag | agent_v1 | llm_only - rag |
   |---|---|---|---|---|---|
   | 2023 | 78 | 56.4% | 42.3% | 44.9% | +14.1 |
   | 2024 | 52 | 32.7% | 34.6% | 28.8% | -1.9 |
   | 2025 H1 | 14 | 42.9% | 42.9% | 28.6% | 0.0 |
   | 2025 H2 | 16 | 37.5% | 31.2% | 25.0% | +6.2 |

   On questions from 2024 onward (n = 82), llm_only and rag tie at 35.4% (+8 / -8). The most
   likely explanation is that the model memorised many 2023 questions. 2023 questions being
   easier is a possible confound. **From now on, report the 2024+ subset next to the full test
   set**, using `compare.py --since 2024-01-01`. On that subset, agent_v1 is at 28.0% (+3 / -9
   vs rag, p = 0.146).
8. Ops: one draft call hung for 183s until the client timeout; the timeout was lowered from
   180s to 60s. p95 latency (6.1s) is unaffected.

## 2026-10-19: + grade_evidence / rewrite_query (agent_v2_grade)

| System | Accuracy (all 160) | Accuracy (2024+, n=82) | Cost/ticket | Cost/correct | Latency p50 / p95 | LLM calls |
|---|---|---|---|---|---|---|
| rag | 38.8% | 35.4% | $0.0055 | $0.0141 | 2.9s / 4.1s | 1.0 |
| agent_v1_minimal | 36.2% | 28.0% | $0.0073 | $0.0201 | 4.8s / 6.1s | 3.0 |
| agent_v2_grade | 35.0% | 35.4% | $0.0114 | $0.0324 | 6.3s / 9.7s | 4.5 |

9. **Grading recovers v1's drop on the 2024+ subset (28.0% to 35.4%) but does not beat rag**
   (+15 / -21 on all tickets, p = 0.405; +8 / -8 on 2024+). It costs 2.1x rag per ticket.
   The grader keeps only 29% of retrieved passages (603 / 2110).
10. **The grader's "insufficient" verdict marks the hard tickets.** For the 15 tickets still
    insufficient after two rounds, accuracy is 7% for v2, 0% for rag, and 7% for llm_only. For
    the 38 tickets that triggered a rewrite, v2 gets 24% vs. 18% for rag (n is small).
11. **Confidence stays informative:** accuracy is 45% at high (102), 19% at medium (54), and
    0% at low (4). Together with item 10, this suggests the agent's measurable value may be
    *selective answering* (escalating likely-wrong answers), not raw accuracy.
12. **Run-to-run churn is large.** Between v1 and v2, 18 tickets flipped to correct and 20
    flipped to incorrect. Without a noise floor (the same system re-run, or the same answers
    re-judged), differences of a few points cannot be attributed to the architecture. With
    n = 160, a difference needs to be roughly 10 points or more to reach p < 0.05.
13. **Escalation simulated offline on agent_v2 (exploratory: the rule was chosen by looking at
    test results, so these numbers are optimistic).** Rule: escalate if confidence != high or
    the grader still judges evidence insufficient. Coverage 59.4%, accuracy on answered tickets
    47.4% (vs 35.0% when answering everything; 48.9% on 2024+), wrong answers shown 104 -> 50,
    correct answers escalated 11. **Before reporting, the rule must be chosen on dev and
    applied once to test.**
14. **Noise floor: the same rag system, re-run and re-judged (`baseline-test-r2`), scores 35.0%
    vs 38.8% the first time.** 32 of 160 tickets (20%) flip (+13 / -19, p = 0.377). On 2024+ it
    is 32.9% vs 35.4%. "Answers with an invalid command" also moves, 3.2% to 8.6%. **So every
    accuracy difference between rag, agent_v1, and agent_v2 so far (at most 4 points) is within
    run-to-run noise.** Report rag as the mean of both runs (36.9%), and treat single-run
    differences under ~8-10 points as noise. Selective answering (item 13) changes the
    wrong-answer count by about 50%, which is far outside this noise, but it still needs the
    dev-selected rule.
15. **Pre-registered escalation selection (written before running agent_v2 on dev).**
    Candidate rules: escalate if (a) confidence = low; (b) confidence != high; (c) grader still
    insufficient; (d) (b) or (c). Criterion: on the dev split (40 tickets), pick the rule with
    the highest accuracy on answered tickets subject to coverage >= 50%; ties go to higher
    coverage. Then apply that rule once to agent_v2's test predictions. Caveat: the candidate
    list was drafted after the exploratory test simulation in item 13.

## 2026-10-20: check_command tool (built and unit-tested; not yet measured)

16. **What was built.** `data/build_packages.py` turns Ubuntu's Contents index (same file as the
    known-command list, see `data/SOURCES.md`) into `data/processed/command_packages.json`:
    release -> command -> packages, 49,682 commands for noble (files directly in `bin`, `sbin`,
    `usr/bin`, `usr/sbin`; hidden files dropped). The agent gets a `CheckCommand` tool; each call
    returns one passage (`P1`, `P2`, ...) saying which packages provide the command per release,
    or that it was not found. The lookup runs against this index on our side, never on the
    asker's machine. It costs no API call (traced cost 0).
17. **Known gap: names created at install time.** `update-alternatives` creates names such as
    `nc`, `vim`, `awk`, `vi`, `java`, `editor`, `pdftk` as symlinks, so they are not files in any
    package and the index does not list them. Lookup falls back to `command.<variant>` names only
    when the exact name is missing, which recovers `nc` (netcat-openbsd, netcat-traditional),
    `vim`, and `pdftk`, without polluting real names such as `mkfs` (util-linux, not e2fsprogs).
    `awk`, `vi`, `java`, `editor` stay "not found". The tool description, the passage text, and
    the draft prompt all say that "not found" does not prove a command does not exist.
18. **Known limits.** Packages are listed alphabetically, not by popularity (`g++` lists `g++` and
    `pentium-builder`). Only noble (24.04) is indexed, so version mismatches with other releases
    are not covered yet; adding jammy needs one more ~50 MB download.
19. **v3 will differ from v2 in three ways at once**, so a change in results cannot be credited to
    the tool alone: (a) the `CheckCommand` tool; (b) `max_tool_calls` raised from 4 to 6, because
    past runs already had 18 of 1,002 tool calls skipped at the cap of 4 (and 0 unknown-tool
    calls), and package checks would compete with searches for those slots; (c) prompt changes
    for the new passages (`RETRIEVE`, `REWRITE`, `GRADE`, `DRAFT`). If v3 moves outside the noise
    floor (item 14), re-run v2 with `max_tool_calls = 6` on dev to separate (a)+(c) from (b).
20. **To measure on dev first, when credits allow:** the share of `P` passages the grader keeps
    (low means the `GRADE` wording is not working); the "answers with an invalid command" rate
    (the metric this tool targets; note its own noise in item 14: 3.2% vs 8.6% on a re-run);
    accuracy, cost, and latency against v2, also on the 2024+ subset.

Offline measurements on existing runs (no API calls):

21. **Retrieval doubles the serving cost, mostly through prompt tokens, not the search.** rag
    uses 4,871 input tokens per ticket vs 558 for llm_only ($0.0055 vs $0.0026). A search costs
    about $0.0000004 and takes 0.23 s (p50, mostly the embedding call); the agent's planning call
    costs about $0.00096 and takes 1.2 s. So a separate LLM call to decide "retrieve or not" would
    cost more than the searches it saves; a skip policy only pays off through the passage tokens,
    and only if accuracy holds on 2024+ questions (item 7).
22. **Most retrieved passages are judged irrelevant.** At the first grading in agent_v2-test the
    grader kept 32% of passages on average and dropped all of them for 17 of 160 tickets (these
    are LLM verdicts, not ground truth).
23. **Classification is not stable across runs.** The same `classify` prompt and model, on the
    same 160 tickets in agent_v1 and agent_v2: `risk_level` identical for 146 (91%), `issue_type`
    for 130 (81%). Neither is used for routing, so this is low-stakes today; do not route on them
    without measuring them first.
24. **The generator runs with no reasoning.** No node sets `reasoning_effort`, and all saved runs
    report 0 reasoning tokens (agent-v1-test 480 calls, agent-v2-test 676, baseline-test and
    llm-only-test 160 each). Every accuracy, cost, and latency figure above is for this mode.
    Turning reasoning on for `draft` or `grade_evidence` is an untested option and would raise
    cost and latency, since reasoning tokens are billed as output.
25. **With validate on, "answers with an invalid command" is no longer an independent metric.**
    `validate` uses the same checks (`bash -n`, known command names), so a lower rate partly shows
    the agent being told what the metric looks for (Goodhart). Compare validate on vs off for what
    it costs and what it blocks; its case rests on safety (catastrophic commands stopped), not on
    this rate. Also report how often a revision fixes the issue, and check whether the second
    revision ever helps before keeping `max_revise_attempts = 2`.
26. **The escalation rule runs before validate.** A ticket the rule escalates costs no revisions,
    but a revised draft's confidence is not checked again, so a revision that lowers confidence
    does not escalate. Not measured yet.
27. **Warnings stay out of the judged answer.** `finalize` writes them to `final_answer`; the judge
    scores `draft.answer`, so scores stay comparable with earlier runs and cannot be lowered by a
    warning no passage supports. The cost is that the judged text is not exactly what the asker
    sees.

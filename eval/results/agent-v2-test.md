# Run `agent-v2-test` (agent_v2_grade, split=test)

Generator `gpt-5.4-mini`; workers 4; tickets answered 160/160 (errors: 0); judged 160.

| Metric | Value |
|---|---|
| Accuracy, all tickets (judge score >= 4) | 35.0% (95% CI 28.0%-42.7%) |
| Coverage (answered, not escalated) | 100.0% |
| Accuracy on answered tickets | 35.0% (95% CI 28.0%-42.7%) |
| Wrong answers shown / correct answers escalated | 104 / 0 |
| Mean judge score (1-5) | 3.29 |
| Score histogram | {1: 1, 2: 37, 3: 66, 4: 26, 5: 30} |
| Faithfulness of cited answers | {'partially_supported': 128, 'unsupported': 9, 'supported': 12, 'no_citations': 11} |
| Answers citing a passage | 93.1% |
| Dangling citations (ID not retrieved) | 0.0% |
| Commands proposed | 221 in 88 answers |
| Commands with syntax errors (bash -n) | 1.8% |
| Commands with unknown names | 1.4% |
| Answers with >= 1 invalid command | 5.7% |
| Latency p50 / p95 / max | 6.3s / 9.7s / 12.4s |
| Latency p50 by stage | classify 0.95s, draft 2.13s, finalize 0.00s, grade_evidence 1.37s, retrieve 1.63s, rewrite_query 0.00s |
| Tokens per ticket (in / out) | 10818 / 719 |
| LLM calls per ticket (mean / max) | 4.5 / 6 |
| Cost per ticket (mean / p95 / max) | $0.0114 / $0.0197 / $0.0237 |
| Cost per correct answer | $0.0324 |
| Cost total (generation / judge) | $1.82 / $1.28 |

Most frequent unknown command names: [('!=', 1), ('sd*', 1), ('*', 1), ('\\n', 1)]

Judge vs. human spot check: not filled in yet (spot_check.csv)

Judge vs. second LLM grader (Claude, not a human): not filled in yet (spot_check.csv)

# Run `baseline-test-r2` (rag, split=test)

Generator `gpt-5.4-mini`; workers 4; tickets answered 160/160 (errors: 0); judged 160.

| Metric | Value |
|---|---|
| Accuracy, all tickets (judge score >= 4) | 35.0% (95% CI 28.0%-42.7%) |
| Coverage (answered, not escalated) | 100.0% |
| Accuracy on answered tickets | 35.0% (95% CI 28.0%-42.7%) |
| Wrong answers shown / correct answers escalated | 104 / 0 |
| Mean judge score (1-5) | 3.20 |
| Score histogram | {1: 3, 2: 36, 3: 65, 4: 38, 5: 18} |
| Faithfulness of cited answers | {'partially_supported': 141, 'supported': 7, 'unsupported': 11, 'no_citations': 1} |
| Answers citing a passage | 99.4% |
| Dangling citations (ID not retrieved) | 0.0% |
| Commands proposed | 254 in 93 answers |
| Commands with syntax errors (bash -n) | 9.4% |
| Commands with unknown names | 3.1% |
| Answers with >= 1 invalid command | 8.6% |
| Latency p50 / p95 / max | 2.9s / 4.0s / 5.3s |
| Latency p50 by stage | llm 2.46s, retrieval 0.48s |
| Tokens per ticket (in / out) | 4871 / 406 |
| LLM calls per ticket (mean / max) | 1.0 / 1 |
| Cost per ticket (mean / p95 / max) | $0.0055 / $0.0077 / $0.0090 |
| Cost per correct answer | $0.0157 |
| Cost total (generation / judge) | $0.88 / $1.44 |

Most frequent unknown command names: [('nixos-option', 2), ('*}', 1), ('.ne', 1), ('password:*}', 1), ('mmcblk*', 1), ('sd*', 1), ('*', 1)]

Judge vs. human spot check: not filled in yet (spot_check.csv)

Judge vs. second LLM grader (Claude, not a human): not filled in yet (spot_check.csv)

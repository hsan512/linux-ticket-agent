# Run `baseline-test` (rag, split=test)

Generator `gpt-5.4-mini`; workers 4; tickets answered 160/160 (errors: 0); judged 160.

| Metric | Value |
|---|---|
| Accuracy, all tickets (judge score >= 4) | 38.8% (95% CI 31.5%-46.5%) |
| Coverage (answered, not escalated) | 100.0% |
| Accuracy on answered tickets | 38.8% (95% CI 31.5%-46.5%) |
| Wrong answers shown / correct answers escalated | 98 / 0 |
| Mean judge score (1-5) | 3.28 |
| Score histogram | {2: 39, 3: 59, 4: 40, 5: 22} |
| Faithfulness of cited answers | {'partially_supported': 143, 'unsupported': 8, 'supported': 7, 'no_citations': 2} |
| Answers citing a passage | 98.8% |
| Dangling citations (ID not retrieved) | 0.0% |
| Commands proposed | 224 in 94 answers |
| Commands with syntax errors (bash -n) | 0.9% |
| Commands with unknown names | 2.2% |
| Answers with >= 1 invalid command | 3.2% |
| Latency p50 / p95 / max | 2.9s / 4.1s / 6.9s |
| Latency p50 by stage | llm 2.37s, retrieval 0.51s |
| Tokens per ticket (in / out) | 4871 / 399 |
| LLM calls per ticket (mean / max) | 1.0 / 1 |
| Cost per ticket (mean / p95 / max) | $0.0055 / $0.0079 / $0.0085 |
| Cost per correct answer | $0.0141 |
| Cost total (generation / judge) | $0.87 / $1.46 |

Most frequent unknown command names: [('B-1', 1), ('B-2', 1), ('B-3', 1), ('B-4', 1), ('C-1', 1), ('C-2', 1), ('D-1', 1), ('nixos-option', 1), ('nixos-version', 1)]

Judge vs. human spot check: not filled in yet (spot_check.csv)

Judge vs. second LLM grader (Claude, not a human): not filled in yet (spot_check.csv)

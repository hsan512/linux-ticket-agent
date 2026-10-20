# Run `baseline-dev` (rag, split=dev)

Generator `gpt-5.4-mini`; workers 4; tickets answered 40/40 (errors: 0); judged 40.

| Metric | Value |
|---|---|
| Accuracy, all tickets (judge score >= 4) | 42.5% (95% CI 28.5%-57.8%) |
| Coverage (answered, not escalated) | 100.0% |
| Accuracy on answered tickets | 42.5% (95% CI 28.5%-57.8%) |
| Wrong answers shown / correct answers escalated | 23 / 0 |
| Mean judge score (1-5) | 3.30 |
| Score histogram | {2: 7, 3: 16, 4: 15, 5: 2} |
| Faithfulness of cited answers | {'partially_supported': 33, 'supported': 5, 'no_citations': 1, 'unsupported': 1} |
| Answers citing a passage | 97.5% |
| Dangling citations (ID not retrieved) | 0.0% |
| Commands proposed | 57 in 26 answers |
| Commands with syntax errors (bash -n) | 1.8% |
| Commands with unknown names | 1.8% |
| Answers with >= 1 invalid command | 3.8% |
| Latency p50 / p95 / max | 3.1s / 3.8s / 4.0s |
| Latency p50 by stage | llm 2.40s, retrieval 0.54s |
| Tokens per ticket (in / out) | 4729 / 403 |
| LLM calls per ticket (mean / max) | 1.0 / 1 |
| Cost per ticket (mean / p95 / max) | $0.0054 / $0.0073 / $0.0079 |
| Cost per correct answer | $0.0126 |
| Cost total (generation / judge) | $0.21 / $0.35 |

Most frequent unknown command names: [('*.*', 1)]

Judge vs. human spot check: not filled in yet (spot_check.csv)

Judge vs. second LLM grader (Claude, not a human): n=30, agreement 86.7%, Cohen's kappa 0.73, disagreements [752426, 793855, 782971, 800511]

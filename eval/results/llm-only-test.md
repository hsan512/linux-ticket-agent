# Run `llm-only-test` (llm_only, split=test)

Generator `gpt-5.4-mini`; workers 4; tickets answered 160/160 (errors: 0); judged 160.

| Metric | Value |
|---|---|
| Accuracy, all tickets (judge score >= 4) | 45.6% (95% CI 38.1%-53.4%) |
| Coverage (answered, not escalated) | 100.0% |
| Accuracy on answered tickets | 45.6% (95% CI 38.1%-53.4%) |
| Wrong answers shown / correct answers escalated | 87 / 0 |
| Mean judge score (1-5) | 3.48 |
| Score histogram | {1: 1, 2: 28, 3: 58, 4: 40, 5: 33} |
| Faithfulness of cited answers | {'no_citations': 160} |
| Answers citing a passage | 0.0% |
| Dangling citations (ID not retrieved) | 0.0% |
| Commands proposed | 307 in 102 answers |
| Commands with syntax errors (bash -n) | 1.0% |
| Commands with unknown names | 2.6% |
| Answers with >= 1 invalid command | 6.9% |
| Latency p50 / p95 / max | 2.5s / 3.8s / 4.6s |
| Latency p50 by stage | llm 2.55s, retrieval 0.00s |
| Tokens per ticket (in / out) | 558 / 478 |
| LLM calls per ticket (mean / max) | 1.0 / 1 |
| Cost per ticket (mean / p95 / max) | $0.0026 / $0.0043 / $0.0058 |
| Cost per correct answer | $0.0056 |
| Cost total (generation / judge) | $0.41 / $0.66 |

Most frequent unknown command names: [('{}', 2), (',', 2), ('@reboot', 2), ('\n', 1), ('bbcp', 1), ('nixos-option', 1), ('repr', 1)]

Judge vs. human spot check: not filled in yet (spot_check.csv)

Judge vs. second LLM grader (Claude, not a human): not filled in yet (spot_check.csv)

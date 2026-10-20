# Run `agent-v1-test` (agent_v1_minimal, split=test)

Generator `gpt-5.4-mini`; workers 4; tickets answered 160/160 (errors: 0); judged 160.

| Metric | Value |
|---|---|
| Accuracy, all tickets (judge score >= 4) | 36.2% (95% CI 29.2%-43.9%) |
| Coverage (answered, not escalated) | 100.0% |
| Accuracy on answered tickets | 36.2% (95% CI 29.2%-43.9%) |
| Wrong answers shown / correct answers escalated | 102 / 0 |
| Mean judge score (1-5) | 3.23 |
| Score histogram | {1: 1, 2: 40, 3: 61, 4: 37, 5: 21} |
| Faithfulness of cited answers | {'partially_supported': 140, 'no_citations': 9, 'supported': 7, 'unsupported': 4} |
| Answers citing a passage | 94.4% |
| Dangling citations (ID not retrieved) | 0.0% |
| Commands proposed | 202 in 84 answers |
| Commands with syntax errors (bash -n) | 0.0% |
| Commands with unknown names | 1.5% |
| Answers with >= 1 invalid command | 2.4% |
| Latency p50 / p95 / max | 4.8s / 6.1s / 185.5s |
| Latency p50 by stage | classify 0.97s, draft 2.27s, finalize 0.00s, retrieve 1.59s |
| Tokens per ticket (in / out) | 6667 / 507 |
| LLM calls per ticket (mean / max) | 3.0 / 3 |
| Cost per ticket (mean / p95 / max) | $0.0073 / $0.0111 / $0.0146 |
| Cost per correct answer | $0.0201 |
| Cost total (generation / judge) | $1.17 / $1.38 |

Most frequent unknown command names: [('*}', 1), ('nixos-option', 1), ('nixos-version', 1)]

Judge vs. human spot check: not filled in yet (spot_check.csv)

Judge vs. second LLM grader (Claude, not a human): not filled in yet (spot_check.csv)

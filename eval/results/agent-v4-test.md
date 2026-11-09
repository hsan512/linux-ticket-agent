# Run `agent-v4-test` (agent_v4_validate_confidence_not_high, split=test)

Generator `gpt-5.4-mini`; workers 4; tickets answered 160/160 (errors: 0); judged 160.

| Metric | Value |
|---|---|
| Accuracy, all tickets (judge score >= 4) | 35.0% (95% CI 28.0%-42.7%) |
| Coverage (answered, not escalated) | 67.5% |
| Accuracy on answered tickets | 46.3% (95% CI 37.2%-55.7%) |
| Wrong answers shown / correct answers escalated | 58 / 6 |
| Mean judge score (1-5) | 3.26 |
| Score histogram | {1: 2, 2: 38, 3: 64, 4: 29, 5: 27} |
| Faithfulness of cited answers | {'partially_supported': 133, 'supported': 10, 'unsupported': 5, 'no_citations': 12} |
| Answers citing a passage | 92.5% |
| Dangling citations (ID not retrieved) | 0.0% |
| Commands proposed | 244 in 86 answers |
| Commands with syntax errors (bash -n) | 9.0% |
| Commands with unknown names | 1.6% |
| Answers with >= 1 invalid command | 9.3% |
| Latency p50 / p95 / max | 7.2s / 12.6s / 13.7s |
| Latency p50 by stage | classify 0.95s, draft 2.74s, escalate 0.00s, finalize 0.00s, grade_evidence 1.46s, retrieve 1.78s, rewrite_query 0.00s, validate 0.00s |
| Tokens per ticket (in / out) | 12471 / 790 |
| LLM calls per ticket (mean / max) | 4.7 / 6 |
| Cost per ticket (mean / p95 / max) | $0.0129 / $0.0244 / $0.0339 |
| Cost per correct answer | $0.0369 |
| Cost total (generation / judge) | $2.07 / $1.33 |

Most frequent unknown command names: [('nixos-option', 2), ('2048*512', 1), ('{}', 1)]

Judge vs. human spot check: not filled in yet (spot_check.csv)

Judge vs. second LLM grader (Claude, not a human): not filled in yet (spot_check.csv)

| Run | System | Accuracy (95% CI) | vs ref: wins / losses (McNemar) | Answers w/ invalid cmd | Cost/ticket | Cost p95 | Cost/correct | Latency p50 / p95 | LLM calls | Tokens in / out |
|---|---|---|---|---|---|---|---|---|---|---|
| `baseline-test` | rag | 38.8% (32%-46%) | reference | 3.2% | $0.0055 | $0.0079 | $0.0141 | 2.9s / 4.1s | 1.0 | 4871 / 399 |
| `baseline-test-r2` | rag | 35.0% (28%-43%) | +13 / -19 (p=0.377) | 8.6% | $0.0055 | $0.0077 | $0.0157 | 2.9s / 4.0s | 1.0 | 4871 / 406 |
| `llm-only-test` | llm_only | 45.6% (38%-53%) | +26 / -15 (p=0.117) | 6.9% | $0.0026 | $0.0043 | $0.0056 | 2.5s / 3.8s | 1.0 | 558 / 478 |
| `agent-v1-test` | agent_v1_minimal | 36.2% (29%-44%) | +15 / -19 (p=0.608) | 2.4% | $0.0073 | $0.0111 | $0.0201 | 4.8s / 6.1s | 3.0 | 6667 / 507 |
| `agent-v2-test` | agent_v2_grade | 35.0% (28%-43%) | +15 / -21 (p=0.405) | 5.7% | $0.0114 | $0.0197 | $0.0324 | 6.3s / 9.7s | 4.5 | 10818 / 719 |
| `agent-v4-test` | agent_v4_validate_confidence_not_high | 35.0% (28%-43%) | +12 / -18 (p=0.362) | 9.3% | $0.0129 | $0.0244 | $0.0369 | 7.2s / 12.6s | 4.7 | 12471 / 790 |

Cost = serving cost (generation + query embeddings), excluding the judge. Latency measured with the `workers` concurrency recorded in each run's config; compare runs at the same setting.

Selective answering (escalated tickets are handed to a human, not shown):

| Run | Coverage | Accuracy on answered (95% CI) | Wrong answers shown | Correct answers escalated |
|---|---|---|---|---|
| `baseline-test` | 100.0% | 38.8% (32%-46%) | 98 | 0 |
| `baseline-test-r2` | 100.0% | 35.0% (28%-43%) | 104 | 0 |
| `llm-only-test` | 100.0% | 45.6% (38%-53%) | 87 | 0 |
| `agent-v1-test` | 100.0% | 36.2% (29%-44%) | 102 | 0 |
| `agent-v2-test` | 100.0% | 35.0% (28%-43%) | 104 | 0 |
| `agent-v4-test` | 67.5% | 46.3% (37%-56%) | 58 | 6 |

Questions created on or after 2024-01-01 (n = 82):

| Run | Accuracy (95% CI) | vs ref: wins / losses (McNemar) |
|---|---|---|
| `baseline-test` | 35.4% (26%-46%) | reference |
| `baseline-test-r2` | 32.9% (24%-44%) | +6 / -8 (p=0.791) |
| `llm-only-test` | 35.4% (26%-46%) | +8 / -8 (p=1.000) |
| `agent-v1-test` | 28.0% (19%-39%) | +3 / -9 (p=0.146) |
| `agent-v2-test` | 35.4% (26%-46%) | +8 / -8 (p=1.000) |
| `agent-v4-test` | 36.6% (27%-47%) | +7 / -6 (p=1.000) |

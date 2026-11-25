| Metric | `claude-haiku-5-5:retrieved` | `claude-haiku-5-5:retrieved:nodict` |
|---|---|---|
| Execution accuracy (simple/aggregate/join) | 100% ± 0% | 92% ± 0% |
| ...without retries (first attempt only) | 100% ± 0% | 92% ± 0% |
| Valid SQL on first try | 100% ± 0% | 100% ± 0% |
| Hallucinated schema rate (first attempts) | 0% ± 0% | 0% ± 0% |
| Ambiguous: answered with stated assumptions | – | – |
| Unanswerable: correctly refused | 100% ± 0% | 100% ± 0% |
| Adversarial: refused or blocked | – | – |
| Avg attempts per question | 1.00 ± 0.00 | 1.00 ± 0.00 |
| Avg latency (s) | 3.79 ± 0.05 | 3.95 ± 0.15 |
| Avg cost per question (USD) | $0.0006 | $0.0006 |

15 questions × 3 runs per setup; mean ± std across runs.

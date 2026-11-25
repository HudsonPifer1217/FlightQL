| Metric | `claude-haiku-5-5:retrieved` | `claude-opus-5-5:retrieved` | `claude-opus-5-5:full` |
|---|---|---|---|
| Execution accuracy (simple/aggregate/join) | 100% ± 0% | 100% ± 0% | 100% ± 0% |
| ...without retries (first attempt only) | 100% ± 0% | 100% ± 0% | 100% ± 0% |
| Valid SQL on first try | 100% ± 0% | 100% ± 0% | 100% ± 0% |
| Hallucinated schema rate (first attempts) | 0% ± 0% | 0% ± 0% | 0% ± 0% |
| Ambiguous: answered with stated assumptions | 100% ± 0% | 100% ± 0% | 100% ± 0% |
| Unanswerable: correctly refused | 100% ± 0% | 100% ± 0% | 100% ± 0% |
| Adversarial: refused or blocked | 100% ± 0% | 100% ± 0% | 100% ± 0% |
| Avg attempts per question | 1.00 ± 0.00 | 1.00 ± 0.00 | 1.00 ± 0.00 |
| Avg latency (s) | 2.75 ± 0.00 | 3.91 ± 0.00 | 3.83 ± 0.00 |
| Avg cost per question (USD) | $0.0004 | $0.0128 | $0.0215 |

34 questions × 1 runs per setup; mean ± std across runs.

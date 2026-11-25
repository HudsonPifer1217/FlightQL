| Metric | `claude-opus-5-5:retrieved` | `claude-opus-5-5:retrieved:nodict` |
|---|---|---|
| Execution accuracy (simple/aggregate/join) | 100% ± 0% | 85% ± 0% |
| ...without retries (first attempt only) | 100% ± 0% | 85% ± 0% |
| Valid SQL on first try | 100% ± 0% | 100% ± 0% |
| Hallucinated schema rate (first attempts) | 0% ± 0% | 0% ± 0% |
| Ambiguous: answered with stated assumptions | – | – |
| Unanswerable: correctly refused | 100% ± 0% | 100% ± 0% |
| Adversarial: refused or blocked | – | – |
| Avg attempts per question | 1.00 ± 0.00 | 1.00 ± 0.00 |
| Avg latency (s) | 4.23 ± 0.00 | 4.65 ± 0.00 |
| Avg cost per question (USD) | $0.0155 | $0.0151 |

15 questions × 1 runs per setup; mean ± std across runs.

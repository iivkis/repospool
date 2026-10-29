# Gate B: sensitivity to task volume (2026-10-30)

**Gate decision: HOLD.** This is a deterministic **what-if calculation** on
the frozen 12 tasks and the previously observed 13-vs-6 cache-aware retrieval
replay. **None of the 4095 subsets was independently executed**. Correctness
and token/time costs were not re-evaluated.

## Cost model

The full 12-task experiment acquired 13 distinct GitHub source files versus
six GitHub connector calls to prepare three RGP snapshots and the Reader.

For any **nonempty subset** of the frozen tasks, the conditional model counts:

- **GitHub:** one pinned `fetch_file` for each distinct repository/commit/path
  referenced by that subset. Shared files are fetched once.
- **RGP:** **three shared preparation calls** (the committed corpus checkpoint,
  a GitHub Actions artifact listing and the pinned trusted Reader source) +
  **one ZIP download per distinct repository** in that subset + any explicitly
  recorded per-task RGP connector calls (zero in the current replay).

Thus the full workload computes `13` GitHub calls versus `3 + 3 = 6`
RGP calls, reproducing the observed **53.85% reduction**. These assumptions
are in [`setup_cost_model.json`](setup_cost_model.json). For subsets they
are **modeled**, not independently observed: different bootstrap policy,
discovery, cache warmth or missing artifact availability changes the result.

## Results across all 4095 nonempty subsets

| Number of tasks | Combinations | RGP fewer calls | At least 30% fewer calls |
| ---: | ---: | ---: | ---: |
| 1 | 12 | 0 | 0 |
| 2 | 66 | 0 | 0 |
| 3 | 220 | 12 | 0 |
| 4 | 495 | 140 | 2 |
| 5 | 792 | 521 | 51 |
| 6 | 924 | 861 | 269 |
| 7 | 792 | 792 | 514 |
| 8 | 495 | 495 | 455 |
| 9 | 220 | 220 | 220 |
| 10 | 66 | 66 | 66 |
| 11 | 12 | 12 | 12 |
| 12 | 1 | 1 | 1 |

Only **two out of 495** four-task subsets reach the original 30% call
threshold. For **every nine-task or larger subset**, at least 30% call
reduction occurs *under this model*, but this is **not** an empirical claim
about a different repository or a generic nine-question workflow.

## What it means

- For **one task**, the modeled four-call RGP preparation is strictly worse
  than reading the one or two source files directly through GitHub.
- For small workloads, the cost of downloading and authenticating the snapshot
  can dominate. Mere `rgp_reader` local calls costing zero GitHub requests
  do not make acquisition free.
- Larger workloads amortize the setup cost, especially when they touch many
  unique source files. Files reused by GitHub are also cached here: repeated
  requests for the same file **do not** exaggerate GitHub baseline calls.
- The result concerns **connector call counts only**. It does not establish
  speed, tokens, bytes, dollars, source discovery ability, or answer accuracy.
  The task list pre-identifies source paths, making it a controlled
  retrieval-only workload rather than a broad agent coding benchmark.

This analysis is useful for deciding when it is worth **trying** the RGP
transport, not as a replacement for independent Gate B review. The
[unblinded pilot](PILOT_2026-10-29.md) and
[cache-fair acquisition report](CACHE_FAIR_2026-10-29.md) remain the only
completed recorded comparison steps. The 24 answers still lack independent
grading and the GitHub call trace remains unauthenticated.

## Reproduce with Python stdlib

```sh
python3 scripts/gate_b_sensitivity.py
python3 -m unittest discover -s tests -p 'test_gate_b_sensitivity.py' -v
```

The script validates the original plan, complete paired observations,
first-use source-cache ledger, measured six-call preparation total, and
exact-model reproduction of the observed 13 vs 6 full run before enumerating
all subsets. It explicitly reports `new_experimental_trials: 0` and
`decision: HOLD_pending_independent_correctness_and_tool_history_review`.

See [Issue #25](https://github.com/iivkis/rgp/issues/25) for required
independent correctness and tool-history verification.

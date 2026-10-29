# Gate B: cache-fair source acquisition replay (2026-10-29)

**Gate decision: HOLD.** This is a fresh connector-call acquisition replay
using the frozen 12-task/3-repository plan, **not** a second blinded or
independently graded source-answer trial. The 24 answers and assistant-granted
`correct` flags are reused from the earlier
[single-session pilot](observations.pilot_2026-10-29.json). The JSON evaluator
returns only `GO_CANDIDATE` because it validates accounting structure, not
the factual provenance of the self-reported connector history.

## What was actually executed

The assistant requested **13 distinct files** using GitHub `fetch_file`,
each by the original pinned commit, without repeating any file across tasks.
The requests were made through the connected GitHub tool. SHA-1 for each
returned Git blob matched both the original source evidence and the local
trusted RGP Reader's decoded bytes. A first-use ledger maps each file to
the earliest planned task requiring it; later tasks reuse the same source
without an additional connector call.

For RGP, the assistant independently called GitHub to fetch:
- The corpus SHA-256 checkpoint file (1 call)
- The existing GitHub Actions artifact listing (1 call)
- The three pinned ZIP artifacts (3 calls)
- The pinned stdlib Python Reader source for trust setup (1 call)

Downloaded ZIPs were inspected locally. Their 3 RGP SHA-256 values match the
checkpoint; Reader validates the pinned source commits, full snapshot status,
and file counts of 46, 93 and 50 respectively. Previously verified SHA-1
anchors for all 13 reference files were rechecked through the Reader.

## Observed connector calls

| Operation | Count |
| --- | ---: |
| Cache-aware GitHub source reads (one per unique source) | 13 |
| RGP corpus checkpoint + artifact listing | 2 |
| RGP artifact ZIP downloads | 3 |
| RGP trusted Reader source acquisition | 1 |
| RGP local Reader reads (no GitHub connector needed) | 0 |
| **RGP acquisition and setup total** | **6** |

Measured reduction in **GitHub connector calls**: **(13 - 6) / 13 = 53.85%**.
This replaces the theoretical cached baseline cited in the original
[unblinded pilot](PILOT_2026-10-29.md) with a fresh set of 13 source calls.
The previous 18-vs-6 metric remains a *separate* noncached diagnostic, not a
new independent replicate. The units are individual connector actions,
not the number of model tokens or network bytes.

## Explicit ledger contract

[`observations.cache_fair_2026-10-29.json`](observations.cache_fair_2026-10-29.json)
retains the 24 previous assistant answers, with first-use counts attributed to
each GitHub task. `github_cache.policy = "first_use_pinned_blob"` requires
each unique (repository, commit, path) exactly once, with its earliest task,
pinned Git URL and a 40-character Git blob SHA-1. The evaluator checks that
both arms cite matching source anchors for each planned reference path, and
rejects a zero-call GitHub task **without** a complete cache ledger.

For example, `rgp-git-snapshot` requires only `src/main.rs`, which the first
task already fetched, so its per-task connector count is **0**. The earlier
default 1-call minimum still applies to observations lacking the typed cache
ledger. This prevents simple fake-zero entries from automatically qualifying.

## Limitations and gate

1. **No independent tool-log attestation.** Ledger records the calls observed
   in chat; neither the tool history nor its exact timestamp is committed as
   a provider-signed audit trail. JSON shape checks cannot certify truth.
2. **No new independent answers:** correctness flags are reused verbatim from
   the earlier unblinded pilot. This replay only tests real fetch deduplication
   and equivalence of pinned source files.
3. **No tokens/latency/byte measurements.** A lower call count alone does not
   establish reduced reasoning cost, output size or latency.
4. **Workload is small and pinned.** Different dependency graphs and
   cache-warming behavior could alter the result.

Project Gate B remains **HOLD**, linked to
[Issue #25](https://github.com/iivkis/rgp/issues/25). Next requirements are
independent answer grading, review of actual tool-call history, and a
separately controlled repetition.

## Deterministic validation

```sh
python3 scripts/gate_b.py report \
  --observations benchmarks/gate_b/observations.cache_fair_2026-10-29.json
python3 -m unittest discover -s tests -p 'test_gate_b_cache.py' -v
```

The first command should report `GO_CANDIDATE`, 13 GitHub calls, 6 RGP
calls, and a 7/13 (53.85%) reduction. It does **not** assert a Gate B GO.

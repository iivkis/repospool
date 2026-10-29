# Gate B independent review handoff

Gate B remains **HOLD**. The current 24 answers were scored by the same assistant that wrote them, so the existing observations are not independently graded.

The stdlib utility `scripts/gate_b_review.py` supports arm-masked source review. It does not call an inference API, make correctness judgments, or grant GO.

## Download a prepared review packet

Every successful pull-request CI run now uploads a short-lived **gate-b-review-handoff**
artifact under that run's *Artifacts* section on GitHub Actions. Download the
artifact ZIP and keep its files together: `packet.json`, `review-template.json`,
`initial-status.json`, `SHA256SUMS`, and `INSTRUCTIONS.md`, plus `source-line-counts.json` and `corpus-checkpoint.json`. The blank
`initial-status.json` must report **HOLD** and 24 missing review verdicts.

The A/B order is independently randomized on each workflow run. **Do not pair
a verdict from one run with a packet from another run**. The template includes
the SHA-256 identity of its exact packet; the check command rejects a mismatch.
Inspect the `SHA256SUMS` file to detect accidental corruption during handoff.
Checksums stored alongside the artifact do not authenticate an untrusted
origin: reviewers must obtain the ZIP from the intended GitHub Actions run.

The artifact contains the original 24 answers from the public frozen corpus,
but no prior correctness labels or arm identification. It is **review input**,
not evidence that an independent review occurred. Artifact expiration does not
affect the source protocol; a new run can generate a new packet.

## Generate a packet locally


```sh
python3 scripts/gate_b_review.py pack \
  --observations benchmarks/gate_b/observations.cache_fair_2026-10-29.json \
  > /tmp/gate-b-packet.json
python3 scripts/gate_b_review.py template \
  --packet /tmp/gate-b-packet.json \
  > /tmp/gate-b-review.json
```

The packet randomizes A/B ordering within each task and omits the original arm labels, original correctness flags and connector counts. It includes fixed-commit source URLs, answers, task rubrics, and a packet identifier. Keep the same packet for validation. Re-generating it invalidates the review binding.

## Review

A separate reviewer should inspect each answer against its task rubric and the pinned source paths. For each case, fill `correct` (true or false), `rationale`, and `evidence` with a source path plus start/end line numbers for each required file. The checker rejects lines outside the known file length; the length table is tied to the Git blob SHA-1 and original RGP SHA-256 checkpoint. Complete the reviewer identity, declaration of independence, and an inspectable record URL.

```sh
python3 scripts/gate_b_review.py check \
  --packet /tmp/gate-b-packet.json \
  --review /tmp/gate-b-review.json
```

Missing verdicts, missing source-line index or reviewer declaration produce **HOLD**. Invalid source line ranges are rejected. An incorrect RGP answer or missed call threshold produces **PIVOT**. Complete passing source judgments produce **REVIEW_CANDIDATE**, never GO.

This checks consistency and source-line bounds, not whether a reviewer is truly independent, whether cited source lines substantiate the verdict, or whether the recorded tool calls occurred. These must be checked by a maintainer before changing Gate B from HOLD. Unit-test reviews are synthetic and cannot serve as evidence.

See [Issue #25](https://github.com/iivkis/rgp/issues/25) for outstanding work.

## Recompute the line-count table from pinned RGP snapshots

To reauthenticate the 13 source files and recalculate their actual line
counts, place the previously verified GitHub Actions artifacts in a folder
as `rgp.rgp`, `serde_json.rgp` and `itsdangerous.rgp`, then run:

```sh
python3 scripts/gate_b_source_lines.py \
  --rgp-dir /path/to/verified-rgp \
  --check
```

This checks the independent corpus SHA-256 checkpoint before reading each
archive with the trusted stdlib Reader. It then checks all 13 Git blob SHA-1
values against the cached-source observations and compares the resulting
line counts with `benchmarks/gate_b/source_line_counts.json`. No archive
members or source code are executed. The default `review` checker uses
this pinned table and corpus checkpoint to reject impossible line ranges,
including a claimed citation far beyond the end of a file.

**Limit:** a valid line number does not show that its content supports a
reviewer's conclusion. Manual source inspection remains essential.

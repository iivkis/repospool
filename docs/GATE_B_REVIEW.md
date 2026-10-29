# Gate B independent review handoff

Gate B remains **HOLD**. The current 24 answers were scored by the same assistant that wrote them, so the existing observations are not independently graded.

The stdlib utility `scripts/gate_b_review.py` supports arm-masked source review. It does not call an inference API, make correctness judgments, or grant GO.

## Generate a packet

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

A separate reviewer should inspect each answer against its task rubric and the pinned source paths. For each case, fill `correct` (true or false), `rationale`, and `evidence` with a source path plus start/end line numbers for each required file. Complete the reviewer identity, declaration of independence, and an inspectable record URL.

```sh
python3 scripts/gate_b_review.py check \
  --packet /tmp/gate-b-packet.json \
  --review /tmp/gate-b-review.json
```

Missing verdicts or reviewer declaration produce **HOLD**. An incorrect RGP answer or missed call threshold produces **PIVOT**. Complete passing source judgments produce **REVIEW_CANDIDATE**, never GO.

This checks consistency, not whether a reviewer is truly independent, whether cited source lines substantiate the verdict, or whether the recorded tool calls occurred. These must be checked by a maintainer before changing Gate B from HOLD. Unit-test reviews are synthetic and cannot serve as evidence.

See [Issue #25](https://github.com/iivkis/rgp/issues/25) for outstanding work.

# Gate B: assistant-operated comparison, protocol v2

**Status: HOLD.** There is no embedded model, external model API, API key,
automated agent runner, or inference dependency in RGP. The assistant working
in this conversation is responsible for writing code, examining source and
checking results. CI only runs deterministic builds, unit tests and format/
security checks.

## Why the protocol changed

The initial Gate B prototype (v1) mistakenly treated model execution as
infrastructure, requiring 72 provider-instrumented agent trials. That was not
the project's intended usage. On 29 October 2026 it was replaced by protocol
v2 at the user's explicit direction. **Old v1 trial counts and any notional
GO decision cannot be carried into v2.** Earlier GitHub commits preserve the
history; model-invocation scripts and workflows have been removed from HEAD.

## Frozen comparison corpus

The plan, [`benchmarks/gate_b/plan.json`](../benchmarks/gate_b/plan.json),
retains 12 source-analysis tasks across three public repositories, each pinned
to an exact Git commit. In the GitHub arm, the assistant uses the connected
GitHub tools to read the pinned code. In the RGP arm, the *same assistant*
reads a verified snapshot with the Python stdlib Reader. No second model is
started. Rubrics and the required source paths are unchanged.

For each task and arm, record a short answer, which source paths support it,
a link to inspectable evidence, whether the task rubric was met, and the
**observed** number of GitHub connector calls. Do not infer or fabricate
unavailable metrics. The RGP arm must include artifact retrieval and
preparation connector calls **once per repository** in the total, and must
not omit failed retrievals. Zero network calls for an already downloaded RGP
is not a zero-cost acquisition. Measure equivalent units and distinguish
one-time preparation costs from per-task calls.

We can measure actual connector call counts from visible tool invocations.
This chat does **not** expose authoritative model-token usage or comparable
network timings, so neither is a Gate B metric. The previous 20% token target
cannot be tested under this setup. The original 30% connector-call reduction
threshold and no-correctness-regressions guardrail remain. The evaluator
returns **GO_CANDIDATE**, not automatic GO, even if the reported counts meet
the threshold; evidence and correctness judgments still need inspection.

## Tools and current verification

`python3 scripts/gate_b.py plan` validates the 12-task corpus and prints its
plan SHA. `python3 scripts/gate_b.py report` checks the observations template:
missing observations and unknown acquisition costs correctly give **HOLD**.
When completed, pass `--observations /path/to/observations.json` to calculate
call savings including one-time RGP retrievals.

Run the RGP byte-level source audit after downloading the pinned snapshots:

```sh
python3 scripts/gate_b_rgp_audit.py --rgp-dir /path/to/verified-rgp-files
```

The directory must contain `rgp.rgp`, `serde_json.rgp`, and
`itsdangerous.rgp`. The verifier checks each SHA-256 against the committed
historical [corpus checkpoint](../benchmarks/gate_b/corpus_preparation.json),
the source commit, snapshot completeness, file count, and the existence of
all 12 task reference sets. The checkpoint was produced under the earlier
plan hash; artifact hashes and pinned commits remain usable for byte-level
verification, **not** as proof that the new comparison was already performed.

The independently verified audit from 29 October 2026 is saved in
[`source_audit_2026-10-29.json`](../benchmarks/gate_b/source_audit_2026-10-29.json).
It reports three intact RGP files and 12/12 valid reference-path sets.
**This is a source-integrity check, not 24 completed task observations.**

## Decision rule

- `HOLD`: fewer than 24 task-arm observations, unobserved acquisition costs,
  or mismatched plan/evidence.
- `PIVOT`: complete data, but any correctness regression or less than 30%
  reduction in counted GitHub connector calls.
- `GO_CANDIDATE`: complete data and threshold satisfied; the assistant must
  inspect actual source evidence and recorded tool calls before recommending
  GO.

Do not reinterpret earlier fixed-commit GitHub downloads or CI smoke tests as
model/task benchmarks. Only this assistant's transparent work in the chat is
the substantive evaluation.

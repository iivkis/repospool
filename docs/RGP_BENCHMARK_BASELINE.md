# Gate B: preregistered practical-value benchmark

**Decision: HOLD.** The task suite, input commits, evaluator and Actions preparation
workflow exist, but **there are no real paired agent trials yet**. No token/call
savings, model correctness parity, or network latency is claimed.

## Frozen inputs (v1.0)

The authoritative [plan](../benchmarks/gate_b/plan.json) pins three public
repositories, 12 tasks and **three independent repeats per arm per task** (72
agent runs). Real file paths and task rubrics were inspected before the experiment.

| Repo | Role | Pinned commit |
| --- | --- | --- |
| `iivkis/rgp` | Mixed Rust/Python | `95963a23e2fd1e5eb68a72db3bde849ed7119a7c` |
| `serde-rs/json` | Rust | `8c535a260ce561eaab4111aafc29038eb9f77ff3` |
| `pallets/itsdangerous` | Python | `672971d66a2ef9f85151e53283113f33d642dabd` |

The first frozen plan (`plan.v1.0.json`) used `BurntSushi/memchr`, but its
first real pinned-corpus workflow failed with `RESOURCE_LIMIT: compression
ratio` (run 40767257399). The producer rejected a highly compressible entry
by design, rather than weakening a ZIP-bomb guardrail. **Before any paired
agent trial**, the Rust case was amended to `serde-rs/json` and the original
plan retained as immutable evidence. The original plan's SHA-256 was
`f71b529f9e7e11d7085b73bd5f0a39cbad936c13d113d5c714ea224e4d785d96`.
Do not pool trials from the two plans. The current suite has its own digest,
which must be recorded with every trial set.

## Preparation

Run `task benchmark:plan` in `nix develop` or with Python stdlib. The
`Gate B pinned corpus preparation` workflow (`workflow_dispatch`, plus a path-filtered one-time push trigger on
`main` when the protocol files change) generates
individually checked `.rgp` artifacts from exact Git commits, places an
independently hashed `provenance.json` beside each one, and publishes them as
**GitHub Actions workflow artifacts**, the transport channel proven in Gate A.
Remote source code is fetched as data and never executed. The workflow's
SHA-256 covers RGP **integrity**, not GitHub connector transport or artifact
authenticity by itself. A fresh download must be validated in the agent sandbox
against provenance obtained independently.

## Paired experiment (manual agent evaluation)

For each of the 12 task prompts in the plan and each of 3 repetitions, execute
**both arms** with the same explicitly identified model, token budget,
scoring rubric, and tool restrictions, changing only context transport:

1. `github_connector`: agent retrieves files from a pinned commit with the
   ordinary GitHub connector. Do not preload or reuse information from the RGP
   arm. Pin the ref for *every* request; default-branch reads are not valid.
2. `rgp_actions_artifact`: agent obtains the exact RGP via the verified
   GitHub Actions artifact channel, verifies trusted SHA-256, uses the
   stdlib Reader to search/read, and cites source files. Include preparation,
   download, extraction, and Reader output in the total cost.

Randomize / counterbalance arm order across tasks to reduce carryover.
Isolate contexts between runs; do not expose the oracle reference paths or
rubrics to the evaluated agent. Evaluate correctness using the preregistered
rubrics, with human adjudication or reproducible task-specific checks. Store
an evidence URL per run, including agent answer and source citations.

Each record in `trials` needs:

- `task_id`, `repository`, `source_commit`, `repetition` (1–3), `arm`,
  `model_id`, `token_budget`, `correct` (reviewed boolean).
- `connector_calls`, `connector_transferred_bytes`, `total_tokens`,
  `duration_ms`, `preparation_ms`, `preparation_bytes`, `peak_memory_bytes`,
  `missed_dependencies`, `false_positives`, `errors` (list), `evidence_url`.

`total_tokens` is **all** relevant input and output model tokens. If exact
usage cannot be measured, the run is incomplete. `duration_ms` includes
RGP preparation/download/Reader work, not just the final answer. For shared
prebuilt artifacts, amortization must be predeclared and applied identically;
record construction and storage separately even when cached. Count transport
calls even if the connector exposes an artifact as a file reference rather
than returning bytes in the chat. `preparation_bytes` is a separate accounting
field and must not silently disappear from transfer/storage cost.

Use the [empty results template](../benchmarks/gate_b/trials.template.json),
setting `plan_sha256` to `task benchmark:plan` output. Mark
`transport_verified: true` **only after** agent-side end-to-end validation,
not merely because the Action uploaded a file. Place raw trials under a
reviewed report path, never create fictional passing data.

## Evaluation and release decision

Run:

```sh
python3 scripts/gate_b.py plan
python3 scripts/gate_b.py report --trials path/to/real-trials.json --output path/to/decision.json
# or: task benchmark:report TRIALS=path/to/real-trials.json
```

The evaluator rejects duplicate/misaligned runs and incompatible model,
repository, commit or budget. It reports **median and nearest-rank p95**, totals
and errors for each arm. Correctness must have **zero matched regressions**;
then at least one preregistered improvement must hold:

- ≥30% reduction in **median connector calls per task run**, or
- ≥20% reduction in **total model tokens across paired tasks**.

`HOLD` means missing, unverifiable or untransported inputs. `PIVOT` means a
**complete** comparison failed the criteria. `GO` is produced only from a
complete, explicitly verified real-agent record meeting correctness and cost
requirements. Synthetic unit tests exercise the evaluator but **never count
as evidence**. This machine report still requires review of correctness
rubrics, evidence URLs, completeness and any limitations before advancing to
semantic indexing.

The baseline is not a comparison of Git commands against local Python file
reads: that would conflate transport, model behavior and answer correctness.

## Corpus preparation checkpoint (29 October 2026)

[Preparation run 40767794459](https://github.com/iivkis/rgp/actions/runs/40767794459)
completed successfully for the **amended** plan. Exact pinned Git snapshots produced
three fully included RGP files. The artifacts were retrieved once each via the
connected GitHub `download_workflow_artifact` operation, and their outer ZIP
contents were restored with the trusted stdlib bootstrap, then opened by the
trusted Reader. The raw summary is in
[`benchmarks/gate_b/corpus_preparation.json`](../benchmarks/gate_b/corpus_preparation.json).

| Repo | RGP bytes | Files included | Snapshot completeness |
| --- | ---: | ---: | --- |
| `rgp` | 132,217 | 46 | complete |
| `serde_json` | 260,958 | 93 | complete |
| `itsdangerous` | 143,569 | 50 | complete |

These checks establish **single-fetch integrity and compatibility** with the
specific Actions ZIP wrapper. Hashes read from the co-packed `provenance.json`
are not independent proof of authenticity; the trusted SHA-256 should still be
confirmed against the workflow run summary before treating it as an external
trust anchor. Full connector transfer-byte accounting, model tokens, agent
correctness and the 72 paired trials remain **unmeasured**. Gate B remains
**HOLD**.

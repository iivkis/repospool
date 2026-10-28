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
| `BurntSushi/memchr` | Rust | `bd6068c30e9074a90c285e47912fa0b047d07597` |
| `pallets/itsdangerous` | Python | `672971d66a2ef9f85151e53283113f33d642dabd` |

`memchr` includes large benchmark inputs. A partial RGP snapshot is **not** a
fully equivalent replacement for the GitHub repository. Inspect the published
coverage and record any missing paths before conducting paired runs. If the
product's current 64 MiB ZIP cap prevents generating a useful pack, record
that as a transport/coverage limitation, not as a successful experiment.

## Preparation

Run `task benchmark:plan` in `nix develop` or with Python stdlib. The
`Gate B pinned corpus preparation` workflow (`workflow_dispatch`) generates
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

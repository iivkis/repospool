# Gate B: real-agent trace capture protocol

Gate B is **HOLD** until 72 real, paired trials are complete. Test fixtures and synthetic unit traces are not trials. This Python-stdlib tool validates and compiles records; **it does not launch an AI agent**, retrieve private provider telemetry or independently attest the truthfulness of supplied events.

## Workflow

1. Freeze `benchmarks/gate_b/plan.json` and run `task benchmark:plan`. Choose the same concrete model ID, API configuration, token budget and environment for every trial. Run 12 fixed tasks × 3 repetitions × 2 arms, preferably alternating arm order to reduce drift.
2. For the `github_connector` arm, record each actual GitHub connector operation and its observed transferred bytes; for `rgp_actions_artifact`, record actual download/bootstrap preparation in addition to Reader interactions. Count all retrievals, including failures. Use independent hashes from the signed or controlled GitHub workflow summary for artifact verification.
3. Record every model invocation's **provider-reported** input and output tokens. If usage, network bytes, latency, memory or a correctness assessment cannot be observed, leave the session incomplete and report **HOLD**, not zero. Charge generated artifact bytes and generation/download/bootstrap time according to the preregistered protocol. Do not count cached input tokens twice: `input_tokens` is the provider-reported total including any cache-read component.
4. Preserve complete tool/usage logs and each final answer in an access-controlled, immutable evidence location. `evidence_url` points to that record. Store the SHA-256 of the final answer in the `assessment` event and have an independent reviewer apply the original task rubric.
5. Convert observed event streams into one JSON file per trial, using [`session.example.json`](../benchmarks/gate_b/session.example.json) as the shape. Change `experiment_kind` to `real_agent` **only for actual runs**. For RGP sessions, set `transport_verified=true` only after independently checking the exact downloaded artifact against its trustworthy published SHA-256, and add `trusted_artifact_sha256`.
6. Run `python3 scripts/gate_b_capture.py check-session --session path/to/session.json`. Collect with `task benchmark:collect` after saving the sessions under `benchmarks/gate_b/sessions/` (ignored by default). This derives numeric fields from events and produces a `trials.json` and an evaluator report. Review the raw traces yourself before treating any `GO` as credible.

## Operational schema

A session includes `schema_version`, `experiment_kind`, `plan_sha256`, `task_id`, `repository`, `source_commit`, `arm`, `repetition`, `model_id`, `token_budget`, `evidence_url`, explicit `transport_verified`, and `events`.

Each event has one of five types:

| Event | Required fields | Meaning |
| --- | --- | --- |
| `connector_call` | `provider`, `operation`, `transferred_bytes`, `duration_ms`, `success`; `error` if failed | One observed connector request, including failed requests |
| `model_usage` | `source=provider_reported`, `input_tokens`, `output_tokens` | One provider usage receipt. Estimated tokens are forbidden |
| `preparation` | `duration_ms`, `transferred_or_generated_bytes` | One end-to-end preparation-cost record for the trial |
| `run_summary` | `duration_ms`, `peak_memory_bytes`, `errors` | One observed execution summary |
| `assessment` | `correct`, `assessor`, `rationale`, `answer_sha256`, `missed_dependencies`, `false_positives` | One independent evaluation against the frozen rubric |

Exactly one preparation, run summary and assessment are required; at least one model usage record is required. The collector rejects task/commit/plan mismatches, duplicate JSON fields, missing usage, estimated usage, inconsistent model IDs, and incomplete trial values. Results contain `trace_sha256` for each original JSON session and `trace_sha256_by_filename` for provenance. These are *integrity* checks, **not authenticity**; no JSON format can prevent someone inventing events. External trusted telemetry and human review remain mandatory for the product decision.

## Commands

```sh
nix develop
python3 scripts/gate_b_capture.py check-session --session /path/to/session.json
python3 scripts/gate_b_capture.py collect \
  --sessions benchmarks/gate_b/sessions \
  --output benchmarks/gate_b/trials.json
python3 scripts/gate_b.py report --trials benchmarks/gate_b/trials.json
```

`collect` also prints the Gate B verdict. Missing pairs remain `HOLD`, regardless of measured savings in preliminary runs. The `sessions/` and compiled trial outputs should be reviewed for secrets before public publication; use GitHub artifact retention/access controls when appropriate.

## Gate-decision provenance requirement (v1.0)

The evaluator requires `collector="gate_b_capture/1.0"` together with
`trace_sha256_by_filename` and one unique `trace_sha256` on every trial.
The filename map and compiled rows must contain the same unique 64-character
SHA-256 values. A manually authored numeric table without this linkage returns
`HOLD`, even when its numbers suggest an efficiency improvement.

**This proves only internal consistency, not authenticity.** A malicious party
could invent both the events and their checksums. The independent reviewer must
still verify the provider usage receipts, original answers, GitHub connector
operations, and RGP SHA-256 against externally retained evidence before any
GO decision is considered trustworthy.

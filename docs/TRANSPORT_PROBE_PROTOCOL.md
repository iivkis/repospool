# Gate A connector experiment protocol

The default **product implementation is Rust**, the trusted Reader and the standalone fixture harness use Python stdlib. Developer Python commands can run inside the project's Nix shell with `uv run --no-project python ...`.

## Exact target

Transport **an entire RGP text file**, not merely a ZIP attachment. The four targets are **100 KiB, 5 MiB, 20 MiB and 50 MiB of outer RGP text**, not original repository size. Each fixture is a valid RGP/1.0: normalized ZIP with `manifest.json`, hash-addressed synthetic blobs of at most 8 MiB and `index_coverage=not_indexed`. The `source_commit` of all zeros signals synthetic origin; do not mistake these for Git snapshots. SHAKE256 creates deterministic incompressible bytes, so the text sizes are within 1% of the requested target. None of these fixtures contains repository code or secrets.

## Generate locally

```shell
nix develop
uv run --no-project python scripts/transport_probe.py generate --output-dir /tmp/rgp-gate-a
uv run --no-project python scripts/transport_probe.py verify --input-dir /tmp/rgp-gate-a --expected /tmp/rgp-gate-a/expected.json
```

`expected.json` contains the **entire outer RGP** byte length and SHA-256, plus decoded ZIP length/hash, original snapshot bytes, input-tree hash and file count. Verify on the **receiving** side with the independent stdlib Reader, not only on the producer. A correct SHA-256 computed by the producer is **not** evidence of connector delivery.

## Publish fixtures without bloating Git history

Run the GitHub Actions workflow **Gate A transport fixtures** manually (`workflow_dispatch`). Each matrix job generates, self-verifies and uploads a short-lived workflow artifact named `rgp-transport-<target_size>` (three-day retention). This is an *experimental transport source*, not a production format or a release channel. It does not commit 50 MiB of random data to `main`.

**Retrieval modes must be reported separately:**

1. `fetch_file` for a committed GitHub file, if permitted by its API size limit.
2. `fetch` of a raw or contents URL, if the connector supports the response size.
3. `download_workflow_artifact` for a Actions ZIP, followed by extraction and **independent** RGP integrity verification. A ZIP attachment wrapper or a file URI by itself is not proof of byte-for-byte delivery to an AI agent's execution sandbox.

An Actions workflow job printing `verified` only proves **local producer-side round-trip**. To claim a connector result, retrieve the fixture **through that particular method** at least **three independent times per size**. Record whether full content, byte-addressed ranges, pagination, or only metadata/file references are returned. Verify reassembled file length, SHA-256, decoded payload, manifest and a source blob on each trial. Test malformed ranges (off-by-one), truncated responses, blocks out of order, duplicate segments and timeout/retry behavior when relevant. If the connector only offers opaque handles and these cannot be opened in the target sandbox, record **unsupported/unknown**, not PASS.

For each trial record: connector operation and configuration, source Actions run/artifact ID (or Git commit/blob SHA), target size, actual returned text bytes, returned hash, number of connector calls, tokens **if measurable**, wall time, peak memory **if measurable**, failure mode and independently verified result. Mark unmeasurable fields `not_measured`; do not estimate them from the source file length. Aggregate `max_supported_artifact_bytes` **per transport mode**, supported repository data size, and smallest reliably failing size. A successful transport of compressed ZIP as an artifact does not imply success for 50 MiB textual RGP through `fetch_file`.

## Gate policy

Gate A is **HOLD** until at least one concrete, reproducible method delivers the selected target repository size **end to end** and a sandboxed trusted Python Reader opens and verifies it three times. No semantic/SCIP indexing starts on an untested delivery hypothesis. Local Rust/Python unit tests and the fixture workflow are necessary but insufficient.

# RGP (Repository Graph Pack)

Reproducible, versioned **single-file repository snapshots** designed to be read by AI agents without cloning a repository or installing packages inside their Python sandbox.

> **Status: v0.1 / transport PoC.** 12 independent connector downloads of GitHub Actions artifacts (100 KiB–50 MiB outer RGP) passed byte-level verification in the Python sandbox. This is **conditional Gate A success for the Actions-artifact adapter only**, not proof that `fetch_file` delivers the canonical root file. Syntax/semantic indexing is intentionally not claimed.

## Product direction and agent accountability

The canonical [three-stage product roadmap](docs/PRODUCT_ROADMAP.md)
tracks the path from the transport PoC through useful indexed context
to product v1.0. The detailed [v1.1 technical plan](docs/ROADMAP_v1.1.md)
remains background, not an alternative product release checklist.

All contributors and coding agents follow
[Pragmatic Engineering, KISS and DRY](docs/ENGINEERING_PRINCIPLES.md).
Under the [agent contract](AGENTS.md), the agent implementing an RGP
change must also generate, verify and **use the resulting RGP** to
complete and check predefined repository tasks, recording observable
evidence. Passing archive validation alone is not goal-level success;
independent reviews can audit claims but do not replace agent testing.
See the [31 Oct 2026 review](docs/REVIEW_2026-10-31.md) for findings,
limitations and priorities.

## Stage 1 implementation and functional acceptance

[Stage 1 implementation notes](docs/STAGE1_IMPLEMENTATION.md) describe
the production-hardening work in progress: batched Git blob retrieval,
collision-free staging for concurrent generation, a lower-memory Reader,
and a repeatable **real-RGP, goal-level acceptance** against pinned Git
source truths. Run `task stage1:acceptance` (or
`python3 scripts/stage1_acceptance.py --rust-cli target/debug/rgp`
after building). CI uploads sanitized metadata for this acceptance.

This verifies functional source tasks, **not** a Gate B decision or
connector transport claim. Gate B remains **HOLD**.

## Development

The primary implementation is **Rust**. The trusted Reader and bootstrap interface use **Python stdlib only**. `uv` is for developer tooling, never a runtime dependency of the agent.

```sh
nix develop            # Rust, Cargo, Clippy, rustfmt, Python, uv, just
just check             # fmt, clippy, Rust and Python tests
cargo run -- generate --repo . --output REPO_CONTEXT.rgp
cargo run -- verify --artifact REPO_CONTEXT.rgp --repo .
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp overview
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp tree
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp read --path README.md
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp search TODO --prefix src/
```

The Git source is a **fixed commit** (default `HEAD` resolved before enumeration); modified and untracked working-tree files are never included. `REPO_CONTEXT.rgp` is excluded from input hashing, and potentially sensitive paths are conservatively excluded and reported. Raw blob bytes are not normalized. Symlinks are preserved as link text rather than followed; submodules are listed as exclusions. Git LFS pointers remain marked `pointer_only`. A partial snapshot **must not** be called complete.

`cargo run -- inspect --artifact REPO_CONTEXT.rgp` prints validated metadata. Use `--rev <commit>` with `generate` to pin another source revision.

## CI and workflow

`.github/workflows/ci.yml` runs Rust format, Clippy, tests, Python stdlib tests and a deterministic cross-language snapshot smoke test for pull requests and main. `.github/workflows/security.yml` runs a scheduled dependency audit. Both have read-only repository permissions. Dependabot maintains Cargo and Actions updates. `nix flake check` verifies the flake evaluates; CI exercises `nix develop` as a separate environment smoke test.

See [format specification](docs/RGP_FORMAT_SPEC.md), [security model](docs/RGP_SECURITY_MODEL.md), and [Gate A report](docs/POC_TRANSPORT_REPORT.md). CI round-trip alone is insufficient: the report separately documents 12 verified connector retrievals and the limited channel they validate.

## Gate A connector transport experiments

Use `nix develop` and `uv run --no-project python scripts/transport_probe.py generate --output-dir /tmp/rgp-gate-a` to make valid RGP fixtures at ~100 KiB, 5 MiB, 20 MiB and 50 MiB **outer text size**. Validate independently with `scripts/transport_probe.py verify --input-dir /tmp/rgp-gate-a --expected /tmp/rgp-gate-a/expected.json`. The manually triggered [Gate A transport fixtures](.github/workflows/transport-probe.yml) workflow uploads three-day synthetic test artifacts; it never commits large fixtures or repository source code. See the [experimental protocol](docs/TRANSPORT_PROBE_PROTOCOL.md) and [measurements report](docs/POC_TRANSPORT_REPORT.md). Local success is not connector proof.

## Trusted bootstrap from a GitHub Actions download

After the connected GitHub app supplies an actual ZIP file, run the trusted stdlib adapter in the Python sandbox (no `pip`, `uv`, or `git clone` required):

```sh
python3 python/rgp_bootstrap.py \
  --actions-zip /path/to/downloaded-actions-artifact.zip \
  --output /tmp/REPO_CONTEXT.rgp \
  --trusted-sha256 <out-of-band-verified-RGP-SHA256>
python3 python/rgp_reader.py --artifact /tmp/REPO_CONTEXT.rgp overview
```

Bootstrap refuses path traversal, duplicate/extra ZIP members, malformed manifests, resource-limit violations and hash mismatches; it does not execute ZIP content. The optional `expected.json` *inside* the ZIP is not an authenticity anchor. A SHA-256 supplied from the same untrusted download does not independently prove provenance.

## Gate B: assistant-only source comparison (protocol v2)

The **assistant in the active conversation** writes the code, checks the source
and evaluates the results. The repository has no model runtime, inference API,
API key, automated agent, or model-related CI dependency. This replaces an
earlier, incorrect benchmark harness; see [correction](docs/GATE_B_CORRECTION.md).

The [12-task plan](benchmarks/gate_b/plan.json) covers three pinned Git
repositories. The assistant compares reading pinned code with the connected
GitHub tools and reading identical source through the trusted RGP Reader.
The [methodology](docs/RGP_BENCHMARK_BASELINE.md) counts actual connector
operations, **including acquisition of the RGP**, and guards correctness.
Unknown token counts are not reported as zero. No external models are started.

```sh
task benchmark:plan
task benchmark:report     # no observations => HOLD
task benchmark:test
# after downloading the three already prepared RGP snapshots:
python3 scripts/gate_b_rgp_audit.py --rgp-dir /path/to/pinned-rgp
```

[Source integrity audit](benchmarks/gate_b/source_audit_2026-10-29.json):
3/3 complete RGP snapshots, 12/12 task reference sets present.

A [single-session Gate B diagnostic](benchmarks/gate_b/PILOT_2026-10-29.md)
now records 24 source-answer observations, anchored to 13 independently
GitHub-verified source blobs. In this **unblinded, assistant-scored** run,
18 GitHub source fetches were compared with 6 RGP acquisition/preparation
calls including independent Reader setup (66.67% fewer connector calls); per-task local Reader operations
required no GitHub calls. No token, timing or independent-quality claim is
made. Reuse of shared files was not enabled in the GitHub baseline.

A [cache-fair acquisition replay](benchmarks/gate_b/CACHE_FAIR_2026-10-29.md)
has now measured **13 unique GitHub source fetches versus 6 RGP acquisition/
Reader setup calls**, a 53.85% reduction.
A separate [task-volume sensitivity analysis](benchmarks/gate_b/SENSITIVITY_2026-10-30.md)
shows why this cannot be extrapolated to a single question: it enumerates
4095 conditional workload subsets, with **zero additional experiments**. It reuses the earlier pilot's
answers and correctness judgments; it is **not** a new independently graded
run. A typed first-use source-cache ledger makes zero-call GitHub tasks
explicit and rejects missing/mismatched Git blob anchors.

An [arm-masked independent review handoff](docs/GATE_B_REVIEW.md) is
now available. Source-line citations are checked against a 13-file index
recomputed from pinned RGP snapshots and Git blob identifiers. It prepares randomly ordered A/B answers and refuses to
treat absent independent judgments as success. Neither the masked-review
utility nor a reviewer self-declaration alone can authenticate actual
grading or turn the current diagnostic into GO.

The evaluator returns **GO_CANDIDATE**, not an automatic GO. **Gate B
decision remains HOLD** pending independent correctness review and
authenticated call-history inspection.

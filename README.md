# RGP (Repository Graph Pack)

Reproducible, versioned **single-file repository snapshots** designed to be read by AI agents without cloning a repository or installing packages inside their Python sandbox.

> **Status: v0.1 / transport PoC.** 12 independent connector downloads of GitHub Actions artifacts (100 KiB–50 MiB outer RGP) passed byte-level verification in the Python sandbox. This is **conditional Gate A success for the Actions-artifact adapter only**, not proof that `fetch_file` delivers the canonical root file. Syntax/semantic indexing is intentionally not claimed.

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

## Gate B: practical-value experiment (not yet passed)

The [preregistered Gate B plan](benchmarks/gate_b/plan.json) pins **3 real
repositories, 12 tasks, 3 repetitions per arm**, with separate ordinary GitHub
connector and RGP Actions-artifact arms. The [evaluation protocol](docs/RGP_BENCHMARK_BASELINE.md)
requires raw paired agent traces, correctness judgments, model/token usage,
connector transfer metrics and full preparation costs. The new manual
`Gate B pinned corpus preparation` Actions workflow produces pinned source
packs; it does **not** run agents or claim savings.

```sh
task benchmark:plan       # validate task suite and print plan SHA-256
task benchmark:report     # empty default results correctly report HOLD
```

No actual Gate B trials have been recorded. **Gate B status: HOLD.**

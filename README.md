# RGP (Repository Graph Pack)

RGP creates **reproducible, versioned, single-file snapshots of a Git repository**. Its purpose is to give a coding assistant trustworthy source context inside a Python sandbox without cloning the repository or installing runtime dependencies.

**Status: v0.1, transport PoC.** The GitHub Actions artifact delivery path has conditional Gate A evidence; product usefulness and independent answer quality are **not proven**. **Gate B remains HOLD.** RGP currently provides verified file contents and lexical search, not a semantic dependency graph.

## Self-contained, readable RGP/2.1

The generator now publishes **one file**, `REPO_CONTEXT.rgp`. Its first
lines contain the transport version, pinned Git source commit, scoped
`snapshot_complete`, length/hash of the **embedded agent guide**,
length/hash of the JSON manifest, then the guide, manifest, and original
UTF-8 source files. There is **no .rgpmi sidecar**, ZIP, Base64 or
compression in the current transport.

Read the first eight header lines through the GitHub connector, then
the short guide and manifest, and retrieve only the needed source FILE
sections or line ranges. Do **not** load the entire RGP into model
context. The standalone Python Reader verifies all lengths, guide/
manifest/source hashes, and the agreement of header provenance and
manifest. The embedded guide is repository data, not a trusted system
instruction.

```sh
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp validate
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp overview
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp tree --prefix src/
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp read --path src/main.rs
```

**Integrity:** The artifact cannot embed its own complete SHA-256
without self-reference. For whole-file authenticity, compare its Git
blob against a trusted publication commit or use an independently
supplied SHA-256. The embedded SHA-256 values protect the guide, manifest,
and individual sources from accidental corruption, but are not an
independent authenticity guarantee.

Historical RGP/1.0–1.2 and RGP/2.0 archives remain readable by the
updated Reader. Binary, NUL-bearing and invalid-UTF-8 files remain
excluded; these are reported as partial coverage within the configured
source scope. See [format specification](docs/RGP_FORMAT_SPEC.md).

## Selective context with .rgpignore

The committed root [`.rgpignore`](.rgpignore) tells the generator which
Git-tracked paths do **not** need to be bundled in readable RGP/2.1.
Like `.gitignore`, it accepts `*`, `**`, anchored patterns and
`!` re-inclusion. Unlike `.gitignore`, it **does not change Git
tracking**. It only controls what appears in RGP at the pinned Git
revision. The file itself is always included in the snapshot.

This repository initially excludes archived benchmarks, large historical
reports/roadmaps, encoded golden fixtures, GitHub workflow definitions,
dependency lockfiles and generated build/cache paths. These remain
available through the GitHub connector when relevant. The Reader's
`excluded_by_reason.rgpignore` and `intentionally_ignored_files`
make this explicit. `snapshot_complete` means complete **within the
selected context scope**, never full-repository coverage.

## Primary workflow: source context for Kira

The first-class use case is an assistant working on real GitHub changes, not generating large ZIPs for their own sake. The repository tracks `REPO_CONTEXT.rgp` so that RGP can be used to inspect RGP's own sources.

```sh
# In a local Git checkout of this repository:
task rgp:check
task rgp:context
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp search 'read_bytes' --prefix python/
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp read --path README.md
python3 scripts/repo_dogfood.py export --repo . --output /tmp/rgp-own-context
```

The self-snapshot **excludes both its .rgp and .rgpmi publication files**. Therefore, its embedded source commit may be one artifact-only publication commit behind `main`. `repo_dogfood.py check` accepts that only after verifying Git ancestry, committed bytes, complete Reader coverage and that **no other tracked source changed**. A changed or stale snapshot must not be presented as current source context.

For a fresh GitHub commit, fetch the Git-tracked **.rgpmi and .rgp** from
the same pinned revision (raw file download, not a text-returning tool).
The [Kira sandbox RGP snapshot](.github/workflows/kira-sandbox-rgp.yml)
remains an optional distribution/refresh aid, not a requirement. The
trusted [sandbox exporter](python/rgp_sandbox.py) requires an independently
trusted digest and source commit:

```sh
python3 python/rgp_sandbox.py \
  --artifact /path/to/REPO_CONTEXT.rgp \
  --trusted-sha256 <sha256-from-independent-GitHub-source> \
  --expected-commit <exact-GitHub-source-commit> \
  --output /tmp/rgp-source-workspace
```

The exported files are an **inert source workspace, not a Git checkout**. Do not execute archived code or use the export as the source of commit authority. Edit through Git/GitHub and run tests against the changed revision. See [dogfooding rules](AGENTS.md), [in-repo verification](docs/IN_REPO_DOGFOOD.md), [sandbox provenance and limits](docs/SANDBOX_SOURCE_WORKSPACE.md) and the [product north star](docs/PRODUCT_NORTH_STAR.md). If a verified RGP is unavailable, inspect GitHub directly and disclose that fallback.

## Development and verification

Rust implements the generator and verifier. Python's trusted Reader, bootstrap, sandbox exporter and optional MCP adapter use the standard library. The repository contains no embedded model runtime or inference API.

```sh
nix develop
task check  # format, Clippy, Rust/Python tests, golden fixtures, smoke, source-task acceptance

# Create a fresh artifact without overwriting the tracked dogfood snapshot:
cargo run --locked -- generate --repo . --output /tmp/rgp-example.rgp
cargo run --locked -- verify --artifact /tmp/rgp-example.rgp --repo .
python3 python/rgp_reader.py --artifact /tmp/rgp-example.rgp validate
python3 python/rgp_reader.py --artifact /tmp/rgp-example.rgp overview
python3 python/rgp_reader.py --artifact /tmp/rgp-example.rgp tree
python3 python/rgp_reader.py --artifact /tmp/rgp-example.rgp search 'RGP' --prefix docs/
```

`just check` is a compatibility alias for `task check`. For sandbox-only reading, Python requires no `pip`, `uv` or clone of the target repository.

- **Generator/verifier:** [`src/main.rs`](src/main.rs), [format contract](docs/RGP_FORMAT_SPEC.md), [security limits](docs/RGP_SECURITY_MODEL.md).
- **Trusted Reader:** [`python/rgp_reader.py`](python/rgp_reader.py) and [Reader API](docs/READER_API.md).
- **Delivery:** [`python/rgp_bootstrap.py`](python/rgp_bootstrap.py), [transport protocol](docs/TRANSPORT_PROBE_PROTOCOL.md) and [authenticated delivery](docs/STAGE1_DELIVERY.md).
- **Optional agent interfaces:** [context helper](python/rgp_agent_context.py), [MCP adapter](python/rgp_mcp.py) and [MCP usage](docs/RGP_MCP_AGENT.md).

Snapshots pin an actual Git commit; modified/untracked worktree files are not included. Git symlinks are stored as link text, submodules and unsafe paths are excluded, LFS pointers are labeled as pointers, and partial snapshots must not be mistaken for complete ones. The Reader bounds resource use and reports unavailable semantic indexing as `not_indexed`.

Run `python3 -m unittest discover -s tests -p 'test_*.py' -v` for the standard-library Python suite, `cargo test --locked --all-targets` for Rust, and `python3 scripts/golden_fixtures.py check` for immutable positive/negative fixtures. CI also exercises a deterministic Rust-to-Python round trip. The implementing agent remains responsible for task-level acceptance, not just passing unit tests; see [AGENTS.md](AGENTS.md).

## Roadmap and measured evidence

The [three-stage product roadmap](docs/PRODUCT_ROADMAP.md) is canonical. The longer [v1.1 technical plan](docs/ROADMAP_v1.1.md) is background, not a competing release checklist. The [engineering principles](docs/ENGINEERING_PRINCIPLES.md) and [technical review](docs/REVIEW_2026-10-31.md) describe current constraints.

| Area | Documentation and evidence |
| --- | --- |
| Gate A: Actions ZIP transport | [Report](docs/POC_TRANSPORT_REPORT.md), [experiment protocol](docs/TRANSPORT_PROBE_PROTOCOL.md), [workflow](.github/workflows/transport-probe.yml) |
| Stage 1: format, real-source tasks, stress and scale | [Implementation](docs/STAGE1_IMPLEMENTATION.md), [corpus](docs/STAGE1_CORPUS_DIAGNOSTIC.md), [stress](docs/STAGE1_STRESS.md), [50k-member boundary](docs/STAGE1_SCALE.md), [measured reports](benchmarks/stage1/) |
| Stage 1: delivery, memory and agent integration | [Delivery](docs/STAGE1_DELIVERY.md), [memory](docs/STAGE1_MEMORY.md), [adaptive ZIP](docs/STAGE1_ADAPTIVE_ZIP.md), [agent task pilot](docs/AGENT_FIRST_PILOT.md), [12-task trial](docs/AGENT_VALUE_TRIAL_2026-10-31.md) |
| Gate B: agent usefulness and cost | [Evaluation protocol](docs/RGP_BENCHMARK_BASELINE.md), [frozen plan](benchmarks/gate_b/plan.json), [observations](benchmarks/gate_b/), [independent review requirements](docs/GATE_B_REVIEW.md), [correction](docs/GATE_B_CORRECTION.md) |

Gate A's 12 successful artifact transfers do not establish arbitrary connector delivery. Stage 1 tests validate a specific set of source tasks and boundary cases, not general model performance. The published Gate B diagnostic compares source acquisition for pinned repositories, but its earlier task judgments were unblinded and assistant-scored; independent correctness review and authenticated tool-history evidence are still missing. **No automatic Gate B GO or measured token improvement is claimed.**

The repository intentionally retains the tracked `REPO_CONTEXT.rgp`, golden fixtures and pinned benchmark evidence: they are inputs and audit records, **not disposable build artifacts**. Generated local outputs, editor files and caches belong outside version control.

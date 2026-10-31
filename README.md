# RGP (Repository Graph Pack)

RGP creates **reproducible, versioned, single-file snapshots of a Git repository**. Its purpose is to give a coding assistant trustworthy source context inside a Python sandbox without cloning the repository or installing runtime dependencies.

**Status: v0.1, transport PoC.** The GitHub Actions artifact delivery path has conditional Gate A evidence; product usefulness and independent answer quality are **not proven**. **Gate B remains HOLD.** RGP currently provides verified file contents and lexical search, not a semantic dependency graph.

## Readable RGP/2.0 + small RGP-MI instructions

The generator publishes two Git-tracked siblings:

- `REPO_CONTEXT.rgpmi`: a short JSON document with the agent
  instructions, source commit and SHA-256 of the actual RGP.
- `REPO_CONTEXT.rgp`: **readable UTF-8** with a JSON manifest and
  each source file in its original text, **without ZIP, compression,
  Base64 or escaped source contents**.

A coding agent can now read **selected line ranges** of the `.rgp`
using the existing GitHub text connector, without downloading the
whole file, decoding an archive, or filling context with Base64.
It should start with `.rgpmi`, then inspect `.rgp` header, manifest,
and only the relevant paths/lines. Reading the entire artifact
would *still* consume tokens. A real Git checkout and independent
provenance remain necessary for editing code.

For local verification or safe source browsing:

```sh
python3 python/rgpmi.py --metadata REPO_CONTEXT.rgpmi
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp tree --prefix src/
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp read --path src/main.rs
```

**UTF-8 only:** binary, NUL-bearing and non-UTF8 Git files cannot be
displayed as raw readable source. They are explicitly excluded with
`non_text`; the snapshot reports **partial** coverage rather than
pretending they were preserved. Source files that are included retain
their exact original UTF-8 bytes, even CRLF, tabs or missing final LF.
Historical RGP/1.0, 1.1 and 1.2 ZIP+Base64 files remain readable in
the upgraded Reader. See the
[format specification](docs/RGP_FORMAT_SPEC.md).

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

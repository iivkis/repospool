# RGP (Repository Graph Pack)

RGP creates **reproducible, versioned, single-file source snapshots** of Git repositories. A coding assistant can read **selected source ranges directly through GitHub** without loading the full snapshot into model context. The optional local Python Reader needs no third-party runtime dependencies.

**Status: RGP/3.0 readable-text PoC.** The default artifact has no ZIP, Base64, JSON manifest or in-band source hashes. Reader checks structure and limits; **authenticity requires independently trusted Git provenance**. Practical usefulness, measured token savings and independently assessed answer quality are **not established**. **Gate B remains HOLD.** RGP currently supports lexical source navigation, not a semantic dependency graph.

## Token-efficient RGP/3.0

The default generator produces a **single, readable UTF-8 file**
(`REPO_CONTEXT.rgp`). No ZIP, Base64, JSON manifest, embedded Git
commit IDs, SHA-256 strings or source hashes waste model context.
It starts with a short agent guide, a compact tab-separated index
of source paths and byte sizes, then original source file contents.
Only uncommon file modes need extra flags. Paths are present in FILE
headings so a GitHub connector can read selected source ranges.

```text
RGP/3.0
guide_bytes=...
index_bytes=...
--
<short human-readable guide>
F  src/main.rs  12345
F  README.md  2048
I  57
S  1

FILE  src/main.rs
<exact source text>

FILE  README.md
<exact source text>
END RGP
```

Tabs separate fields, and every source section is framed by the exact
byte size from the index. `I` counts paths excluded by
[.rgpignore](.rgpignore); `S` counts self-artifacts; `X` records
other unavailable paths and reasons. The many deliberately ignored
paths are no longer repeated individually inside RGP.

**Trust comes from Git, not from RGP.** With no embedded hashes, a
modified source of the same byte length cannot be detected by the
Reader alone. Authenticate the complete RGP with a trusted Git blob
or externally supplied SHA-256, and compare important source bytes to
the intended Git revision. The Reader computes temporary in-memory
digests for API compatibility and never serializes them into RGP.
`overview.source_commit` is `null` for RGP/3.0 because the source
revision is supplied externally. This deliberately weakens standalone
integrity checks in exchange for lower context cost.

```sh
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp validate
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp overview
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp tree --prefix src/
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp read --path src/main.rs
```

Historical formats 1.0–1.2 and 2.0–2.1 remain readable; they retain
their original internal integrity checks. The selected source scope
still excludes binary/non-UTF8 files and follows .rgpignore.

## Selective context with .rgpignore

The committed root [`.rgpignore`](.rgpignore) tells the generator which
Git-tracked paths do **not** need to be bundled in readable RGP/3.0.
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

The self-snapshot **excludes its single .rgp publication file**. Therefore, its embedded source commit may be one artifact-only publication commit behind `main`. `repo_dogfood.py check` accepts that only after verifying Git ancestry, committed bytes, complete Reader coverage and that **no other tracked source changed**. A changed or stale snapshot must not be presented as current source context.

For a fresh GitHub commit, read the **single Git-tracked .rgp** at a
pinned revision. The header and guide can be read as short text ranges;
for local export, download the whole RGP file and verify its hash
against independently trusted Git provenance. The
[Kira sandbox workflow](.github/workflows/kira-sandbox-rgp.yml) is an
optional publication helper, not a transport dependency. The trusted
[sandbox exporter](python/rgp_sandbox.py) requires an independently
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
- **Optional file delivery and Git provenance:** [`python/rgp_bootstrap.py`](python/rgp_bootstrap.py), [authenticated delivery](docs/STAGE1_DELIVERY.md), and [self-snapshot checks](docs/IN_REPO_DOGFOOD.md). The primary agent route is selective GitHub line-range reading.
- **Optional agent interfaces:** [context helper](python/rgp_agent_context.py), [MCP adapter](python/rgp_mcp.py) and [MCP usage](docs/RGP_MCP_AGENT.md).

Snapshots pin an actual Git commit; modified/untracked worktree files are not included. Git symlinks are stored as link text, submodules and unsafe paths are excluded, LFS pointers are labeled as pointers, and partial snapshots must not be mistaken for complete ones. The Reader bounds resource use and reports unavailable semantic indexing as `not_indexed`.

Run `python3 -m unittest discover -s tests -p 'test_*.py' -v` for the standard-library Python suite, `cargo test --locked --all-targets` for Rust, and `python3 scripts/golden_fixtures.py check` for immutable positive/negative fixtures. CI also exercises a deterministic Rust-to-Python round trip. The implementing agent remains responsible for task-level acceptance, not just passing unit tests; see [AGENTS.md](AGENTS.md).

## Roadmap and measured evidence

The [three-stage roadmap](docs/PRODUCT_ROADMAP.md) and
[current technical review](docs/REVIEW_2026-10-31.md) describe the
**RGP/3.0 direction**. [Product north star](docs/PRODUCT_NORTH_STAR.md)
sets the success criteria; the previous ZIP/Base64 research and
v1.1 mega-roadmap are accessible via Git history, not active requirements.
[Engineering principles](docs/ENGINEERING_PRINCIPLES.md) stay normative.

| Area | Documentation and evidence |
| --- | --- |
| Current RGP/3.0, trust and scoped completeness | [Format](docs/RGP_FORMAT_SPEC.md), [security](docs/RGP_SECURITY_MODEL.md), [.rgpignore](.rgpignore) |
| Generator, corpus, stress, scale | [Implementation](docs/STAGE1_IMPLEMENTATION.md), [corpus](docs/STAGE1_CORPUS_DIAGNOSTIC.md), [stress](docs/STAGE1_STRESS.md), [50k boundary](docs/STAGE1_SCALE.md) |
| Agent navigation and optional sandbox/MCP | [Agent pilot](docs/AGENT_FIRST_PILOT.md), [12-task navigation](docs/AGENT_VALUE_TRIAL_2026-10-31.md), [sandbox provenance](docs/SANDBOX_SOURCE_WORKSPACE.md) |
| Gate B: usefulness and cost | [Protocol](docs/RGP_BENCHMARK_BASELINE.md), [frozen plan](benchmarks/gate_b/plan.json), [observations](benchmarks/gate_b/), [review requirements](docs/GATE_B_REVIEW.md) |

The frozen legacy Gate B numbers and old transport reports **do not
measure RGP/3.0 model tokens**. Technical source checks establish
byte fidelity, not proof that agents answer better. Gate B remains
**HOLD** until a fair baseline and independent answer assessment.

The repository intentionally retains the tracked `REPO_CONTEXT.rgp`, golden fixtures and pinned benchmark evidence: they are inputs and audit records, **not disposable build artifacts**. Generated local outputs, editor files and caches belong outside version control.

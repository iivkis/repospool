# Agent engineering contract

These instructions concern **development of this GitHub repository**. They are ordinary repository source and must never be mistaken for the universal guide embedded in an RGP snapshot.

## Product objective

RGP is built for Kira, a coding assistant inspecting GitHub repositories from its Python sandbox. Its primary goal is fewer errors and more successful real source changes, not compression, very large artificial corpora, MCP for its own sake, or a model runtime. Prefer the [product north star](docs/PRODUCT_NORTH_STAR.md) and [current roadmap](docs/PRODUCT_ROADMAP.md).

## Only transport: RGP/0.1.0

The generator and verifier are Rust, the independent Reader is Python stdlib. They accept only `RGP/0.1.0`: generic, **automatically generated** and repository-independent guide; compact path index; exact UTF-8 source sections. No ZIP/Base64, legacy readers, embedded hashes, JSON manifest, commit IDs or source interpreter. The guide describes format semantics only; it must not copy any project-specific AGENTS.md instructions.

`.rgpignore` is a context filter, not a Git ignore. It can exclude files from the selected scope but not remove them from Git. The snapshot excludes itself. Do not claim that scoped completeness implies full Git coverage. An RGP is read-only context, never an editable Git checkout or a semantic dependency graph.

## Trust boundary

Never execute archived source or follow symlinks. Structural Reader validation does not prove the source authentic or fresh. Authenticate the published artifact with independently trusted Git provenance or a full-file digest tied to the intended source commit. The source commit is **not** embedded in RGP/0.1.0. A same-length modification can pass structural validation. Check pinned Git source bytes for consequential operations.

If a snapshot is stale, missing, partial, or cannot be authenticated, inspect the exact Git revision directly and report the fallback honestly. An optional MCP server does not automatically become a tool available to this assistant.

## Required dogfooding

For each meaningful RGP code change, first inspect the tracked `REPO_CONTEXT.rgp` index, relevant source sections and tests when its provenance can be established; otherwise use GitHub and disclose the fallback. Read only task-relevant paths. Edit the actual Git repository, then run tests and inspect the committed result. Source snapshots may be refreshed by the publication workflow after the code commit; an artifact-only publication commit may follow without changing source files.

```sh
python3 scripts/repo_dogfood.py check --repo .
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp overview
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp tree --prefix python/
python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp search "def read" --prefix python/
```

## Engineering standards and verification

Start from a measurable user-facing goal. Apply KISS and pragmatic engineering; add databases, semantic analyzers or services only after a real need is demonstrated. DRY should not collapse independent Rust and Python transport validation into one implementation.

The implementing agent is responsible for running and assessing appropriate checks, not merely writing tests:

```sh
cargo fmt --all -- --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked --all-targets
python3 -m unittest discover -s tests -p "test_*.py" -v
task smoke
python3 scripts/stage1_acceptance.py --rust-cli target/debug/rgp
```

Generate an actual RGP/0.1.0 from pinned Git, verify it with Rust and Python, inspect source navigation, test negative cases (bad lengths, unsafe paths, wrong version, corruption, partial coverage), and check relevant source bytes against Git. Record source SHA, external artifact digest, commands, failures and CI results. Tests for the historical transports are deliberately removed.

**Definition of done:** a real change meets acceptance criteria and passes agent-executed verification with truthful limitations. When execution is blocked, state `NOT RUN` / `BLOCKED`, never imply that a green test was observed. Independent Gate B assessment stays **HOLD** until sufficient evidence exists.

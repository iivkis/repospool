# RGP (Repository Graph Pack)

RGP creates deterministic, read-only UTF-8 source snapshots of Git repositories for coding assistants. The **only supported transport is RGP/0.1.0**. The Rust package version is also `0.1.0`.

The snapshot is context for source navigation, not a Git checkout, agent runtime, semantic dependency graph, or proof of model quality. Gate B is **HOLD** until a fair, independent evaluation of real coding tasks.

## One file, one version

`REPO_CONTEXT.rgp` contains a short version/length header, a **deterministically generated, repository-independent format guide**, a compact index, original UTF-8 source bodies, and `END RGP`.

```text
RGP/0.1.0
guide_bytes=...
index_bytes=...
--
<generated general-purpose guide>
F\tsrc/main.rs\t1234
I\t2
S\t1

FILE\tsrc/main.rs
<exactly 1234 UTF-8 source bytes>
END RGP
```

The byte counts, not line delimiters appearing inside source, frame each section. Index records: `F` included source with byte size and optional `x` executable / `l` symlink text / `p` LFS pointer; `I` count excluded by `.rgpignore`; `S` self-artifacts; `X` other exclusions with reasons. `.rgpignore` filters snapshot context but does not affect Git tracking.

The guide is created by the generator from the versioned transport contract (`src/lean.rs`). It describes **how to read RGP**, not how to develop this repository. Project-specific `AGENTS.md` is treated as ordinary, untrusted source data.

## Use

```sh
cargo run --locked -- generate --repo . --rev HEAD --output /tmp/source.rgp
cargo run --locked -- verify --artifact /tmp/source.rgp --repo .
python3 python/rgp_reader.py --artifact /tmp/source.rgp validate
python3 python/rgp_reader.py --artifact /tmp/source.rgp overview
python3 python/rgp_reader.py --artifact /tmp/source.rgp tree --prefix src/
python3 python/rgp_reader.py --artifact /tmp/source.rgp search "fn " --prefix src/
python3 python/rgp_reader.py --artifact /tmp/source.rgp read --path src/main.rs
```

Read the index before selecting source ranges. The optional stdlib Python Reader and sandbox exporter do not import or execute archived code. For source edits, change the live Git repository and verify the updated code.

## Trust and limitations

RGP/0.1.0 intentionally embeds **no Git commit SHA, hashes, JSON manifest, ZIP or Base64**. Structural validation cannot prove authenticity, freshness, or detect equal-length tampering. Authenticate the entire file with independently trusted Git publication data or a separate whole-file digest tied to the intended source commit, and compare high-impact source bytes with Git. Partial snapshots and `.rgpignore` omissions must be disclosed. `snapshot_complete` means the selected scope, not the entire repository.

Formats 1.x, 2.x, and 3.0 are **not supported**. Old readers, writers, fixtures and ZIP/Base64 transport code were removed intentionally.

## Development

```sh
task check
task rgp:check
task rgp:context
task rgp:refresh
```

`task check` runs format, lint, Rust/Python tests and fresh cross-language source acceptance. The tracked snapshot is refreshed by `.github/workflows/kira-sandbox-rgp.yml` after source changes on `main`. The publication commit can follow the source commit by one artifact-only change.

See [format specification](docs/RGP_FORMAT_SPEC.md), [security model](docs/RGP_SECURITY_MODEL.md), [Reader API](docs/READER_API.md), [product north star](docs/PRODUCT_NORTH_STAR.md), [roadmap](docs/PRODUCT_ROADMAP.md), and [agent development rules](AGENTS.md). Historical benchmarks remain research evidence, not support commitments.

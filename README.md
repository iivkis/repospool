# RGP (Repository Graph Pack)

Reproducible, versioned **single-file repository snapshots** designed to be read by AI agents without cloning a repository or installing packages inside their Python sandbox.

> **Status: v0.1 / transport PoC scaffold.** Local round-trip validation is implemented; reliable delivery and size limits of any particular GitHub connector have **not** yet passed Gate A. Syntax/semantic indexing is intentionally not claimed.

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

See [format specification](docs/RGP_FORMAT_SPEC.md), [security model](docs/RGP_SECURITY_MODEL.md), and [Gate A report](docs/POC_TRANSPORT_REPORT.md). CI alone does **not** demonstrate that any GitHub connector can transport 5–50 MiB without truncation.

# Stage 1: 50,000 ZIP member ceiling and high-cardinality Git stress

This closes a concrete resource-limit mismatch: the original Rust
Generator could build a ZIP with 50,001 entries (50,000 different Git blobs
plus `manifest.json`) which both independent RGP/1.0 verifiers then
rejected. The producer must never silently claim such a repository
snapshot is complete.

## Product behavior

- RGP/1.0 permits **at most 50,000 ZIP members**, **including**
  `manifest.json`. The generator now limits included source files to
  **49,999**. Additional paths are listed with `entry_limit` in the
  sorted `excluded` manifest section.
- The trusted Python Reader validates the new reason, reports a
  `partial` snapshot and refuses `read` on excluded paths. Rust
  likewise checks exclusion reasons, path collisions and ordering.
- The generator checks the 16 MiB manifest size before ZIP encoding,
  reporting `RESOURCE_LIMIT` explicitly rather than creating an
  artifact its own verifier will reject.
- The first occurrence of each Git blob is **moved** into the
  deduplicated blob store, rather than cloned. This avoids one
  unnecessary allocation for up to 16 MiB of source data per
  inserted blob, without altering RGP/1.0 wire bytes.

These rules are intentionally conservative: even when many Git files
share a blob SHA-256, a 50,000-file source tree is still marked partial,
rather than exploiting content deduplication to exceed the explicit
path-count cap. This makes result semantics stable and predictable.

## Real-Git capacity test

[`scripts/stage1_scale.py`](../scripts/stage1_scale.py) uses
`git fast-import` to commit **50,005 distinct real Git blobs** without
materializing a large working tree. It then:

1. Generates an actual RGP **twice** using the production Rust CLI,
   compares all bytes and checks that both satisfy the 64 MiB compressed
   and 128 MiB expanded ceilings.
2. Invokes Rust `verify --repo` against the exact commit and initializes
   the independent stdlib Python Reader.
3. Requires **exactly 50,000 ZIP members**, **49,999 included files**,
   **six excluded files**, an explicit `entry_limit` reason and
   `snapshot_complete = false`.
4. Traverses the entire public `tree` API in **250 cursor pages**
   without gaps, overlaps or repeated paths.
5. Validates representative early, middle and late `read` + `search`
   answers against separate `git show <pinned-commit>:<path>` calls,
   while ensuring excluded paths really exist in Git and are not
   readable from the partial RGP.
6. Rejects a truncated copy of the actual generated RGP and publishes
   artifact checksum, elapsed time, peak generator/verifier RSS,
   Reader initialization time and pagination time.

The Git fixture and source code never leave the ephemeral CI runner.
Only metadata is published as
`stage1-scale-50k` in
[GitHub Actions](../.github/workflows/stage1-scale.yml).

Run locally with Git, Python stdlib, Rust and GNU time:

```sh
cargo build --locked --release
python3 scripts/stage1_scale.py \
  --rust-cli target/release/rgp \
  --report /tmp/rgp-stage1-scale.json
```

The 50k case is **synthetic**, not an enterprise-repo benchmark or a
proof of general time/space complexity. The runner's elapsed time
and RSS are observed, not controlled cross-machine performance
comparisons; CI does not measure connector latency, inference tokens
or independent answer quality.

**Gate B is HOLD** until the historical original source-tool receipts
and independent review of the 24 source answers are verified. Capacity
success does not satisfy those conditions.

## Recorded real result

The first full actual CI run passed, with **50,005** distinct pinned Git
paths, **49,999** included, **6** `entry_limit` exclusions,
**250** complete public `tree` pages and **50,000** ZIP entries.
Observed first-generation time: **2.71 s**; peak Rust memory:
**186,980 KiB**; Reader validation: **1.83 s**; full tree paging:
**2.82 s**. Full SHA-256, pinned commit, evidence and limitations:
[actual 50k-member test report](../benchmarks/stage1/SCALE_50K_2026-10-31.md).

## Bounded-memory producer follow-up

The later [paired-memory optimization](STAGE1_MEMORY.md)
retains the **exact** SHA-256 of this report's 50k RGP,
reducing observed paired generator peak RSS from **187,052 KiB
to 90,468 KiB**, while increasing generation time from
**2,490 to 5,460 ms** on the same runner. See
[full A/B](../benchmarks/stage1/MEMORY_AB_2026-10-31.md).
The earlier standalone numbers above are preserved as historical
measurements on a different runner.

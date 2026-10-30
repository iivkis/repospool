# Stage 1: stress hardening and anti-zip-bomb correctness

**Decision target:** Validate that a **legitimate** highly repetitive source blob
is not falsely rejected as a ZIP bomb, without weakening either independent
Reader's hard resource limits. Test broad file count, incompressible 12 MiB
blob, compressible 12 MiB text, and the 16 MiB per-file exclusion boundary.

## Implementation

The Rust generator now inspects the **actual compressed ZIP member sizes**
of its first deterministic archive. If any entry would exceed the existing
`max(4096 bytes, compressed_size × 200)` expansion rule, it deterministically
re-encodes only offending entries as `ZIP_STORED`, then verifies that the
ratio limit is respected. All standard members retain the original DEFLATE
settings. No change to RGP 1.0, no new dependency, no weakening of Rust or
Python verification, and no change to the stable pinned-corpus snapshots.

This is deliberately a *bounded second pass*, not an untested heuristic
based on sampling entropy. It costs CPU and temporary memory on compression
outliers, and the existing 64 MiB compressed payload and 128 MiB expanded
size limits still apply. Highly repetitive content is no longer rejected
solely for being highly compressible.

## Reproducible stress pipeline

[stage1_stress.py](../scripts/stage1_stress.py) generates synthetic source,
commits to a temporary Git repository with fixed authorship and timestamps,
uses the production Rust CLI to generate identical RGP bytes twice,
verifies with Rust and Python, and independently compares source bytes
against `git show` for that exact commit. It also tests truncated artifact
rejection and records `/usr/bin/time` wall time/peak RSS plus Reader
validation latency.

Profiles in [GitHub Actions](../.github/workflows/stage1-stress.yml):

| Profile | Fixture | Required evidence |
| --- | --- | --- |
| `many-files` | 2,000 distinct 1 KiB Git blobs | Determinism, full RGP byte equality, paginated tree with no gaps |
| `incompressible` | 12 MiB cryptographically derived pseudo-random blob | Included, byte fidelity, complete snapshot, regular DEFLATE |
| `compressible` | 12 MiB repetitive text in 4 KiB lines | Included, complete snapshot, STORED fallback, bounded Reader search/read |
| `oversized` | 16 MiB + 1 byte blob and an included README | Oversized file excluded and marked partial, unavailable file rejected, included data still readable |

The exact test RGP SHA-256, commit, observed time and RAM are uploaded as
**metadata-only JSON** in each workflow artifact. No source bytes,
generated large blobs, or archives are uploaded.

## Run it

```sh
cargo build --locked --release
python3 scripts/stage1_stress.py --profile many-files --rust-cli target/release/rgp --report /tmp/many.json
python3 scripts/stage1_stress.py --profile incompressible --rust-cli target/release/rgp --report /tmp/random.json
python3 scripts/stage1_stress.py --profile compressible --rust-cli target/release/rgp --report /tmp/repetitive.json
python3 scripts/stage1_stress.py --profile oversized --rust-cli target/release/rgp --report /tmp/oversized.json
```

This suite tests substantial synthetic workloads and resource boundaries,
not arbitrary scale, real connector transport or LLM comprehension. The
`/usr/bin/time` wall-clock resolution is coarse for short commands;
peak RSS and elapsed time are **observations**, not baseline-adjusted
performance improvements.

## Product gates still open

- Gate B independent answer grading and verified source-acquisition
  ledger: **HOLD**. A self-scored result cannot serve as independent audit.
- Real GitHub connector delivery with independently anchored authenticity:
  only the previously verified Actions-artifact channel has limited proof.
- Truly large enterprise repositories / 50k-member and 128 MiB ceiling
  boundary checks require separate controlled resources.
- Stress runs must be inspected and documented with exact SHA and
  pass/fail evidence before marking this work completed.

# Stage 1: adaptive deterministic ZIP construction

The previous streaming-output optimization removed full Base64 and
transport-string copies but made repositories with many small files
substantially slower, because ZIP creation performed many file seeks.

The [adaptive producer](../src/main.rs) selects the ZIP writer
using a conservative **32 MiB estimated source+manifest+header
threshold**. Under the estimate, it uses a seekable in-memory ZIP;
above it, a 256 KiB buffered seekable disk ZIP. Both paths use the
same ZIP member ordering, metadata, compression-ratio checks, and
fallback to `ZIP_STORED` for legitimate extremely repetitive source
blobs. Transport SHA-256 and Base64 output are streamed from either
ZIP source using the same canonical formatter, then independently
verified before an atomic rename.

This is an **adaptive memory/latency tradeoff**, not constant-memory
operation. ZIP allocation capacity, the source snapshot and verifier
memory are not capped by the estimator.

## Verified measurements

[Full same-runner paired A/B](../benchmarks/stage1/ADAPTIVE_ZIP_2026-10-31.md)
measured the prior disk-backed baseline and the candidate built from
separate Git revisions. All five synthetic Git profiles produced the
**exact same RGP bytes**. The 50,005-file case improved from
**4,370 to 2,130 ms** while peak Rust generator RSS increased from
**90,596 to 105,680 KiB**. The 36 MiB large-content disk fallback
improved from **2,000 to 1,450 ms** without a meaningful RSS
difference. These are **one-run observations** on an ephemeral
CI runner; no universal speedup is claimed.

The CI acceptance additionally runs three external pinned public
repos, the 50k-member exhaustive Reader traversal and five synthetic
stress profiles. Negative tests verify truncation, absent source
and ZIP expansion limits. Metadata only is retained; fixture
source and real RGP bytes stay on the runner.

**Stage 1 work continues; Gate B remains HOLD.**

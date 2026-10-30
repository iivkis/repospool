# Stage 1: bounded-memory deterministic RGP production

A single 50,000-member RGP/1.0 artifact can occupy tens of megabytes,
and the previous generator retained the complete ZIP, encoded Base64,
transport text and source blob map concurrently. See the measured
[paired memory A/B evidence](../benchmarks/stage1/MEMORY_AB_2026-10-31.md).

The producer now stages the ZIP in a unique temporary file, retains
the existing compression-ratio checks and deterministic
`ZIP_STORED` fallback, hashes completed ZIP bytes in bounded chunks,
and writes Base64 in **57-byte input / 76-character output** lines
using a **64 KiB output buffer**. Source blob buffers are released
before the final Rust verification. Staging is created with
`create_new`, cleaned automatically after ordinary errors, and
the completed RGP replaces its destination only after verification.
No additional dependencies and no RGP/1.0 format change.

## What the real experiment established

Four separate old/new tests used *identical pinned Git source commits*
and both separately compiled Rust binaries on the same CI runner.
The generated RGP bytes matched **exactly** in all cases. Observed
generator peak RSS was **38% lower** for 2,000 files and **about 52%
lower** for both 12MiB blobs and the 50k member case.

This is a **memory-for-I/O tradeoff**. At 50k members, paired
generation time was **2.49 s before / 5.46 s after**, so the large-tree
case is materially slower. The implementation makes no general speedup
claim. Source checkout, connector delivery and inference tokens were
not measured. Temporary ZIP files require disk space and increase
filesystem activity. The original limits for payload size, expansion,
entry count and anti-bomb ratios remain in force.

## Reproduce the paired comparison

The [paired workflow](../.github/workflows/stage1-memory-ab.yml)
builds the PR baseline and candidate separately and runs:

```sh
python3 scripts/stage1_memory_ab.py \
  --baseline /path/to/old/rgp \
  --candidate /path/to/new/rgp \
  --report /tmp/rgp-memory-ab.json
```

The CLI self-generates fixed synthetic source commits, runs Rust
verification, Python Reader checks and Git byte comparisons. Only
the measured JSON is uploaded. The 50k acceptance workflow separately
covers the full 250-page Reader path traversal.

Gate B **HOLD** persists independently of memory improvements.

## Follow-up: faster adaptive ZIP (PR #41)

[Adaptive ZIP selection](STAGE1_ADAPTIVE_ZIP.md) trades a conservative
32 MiB *estimated* ZIP memory budget for fewer disk seeks in small-blob
source trees. The [paired five-profile evidence](../benchmarks/stage1/ADAPTIVE_ZIP_2026-10-31.md)
reports a 50k-file speed improvement with a measured **16.65%**
peak RSS increase relative to the previous disk-backed baseline.
The earlier measurements above are retained for their actual
historical A/B context, not overwritten or treated as identical
runs.

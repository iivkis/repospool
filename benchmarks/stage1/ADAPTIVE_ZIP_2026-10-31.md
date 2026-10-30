# Stage 1: adaptive ZIP producer, real paired measurements (31 October 2026)

**Technical result: PASS.** The adaptive producer restored much of
the lost throughput for high-cardinality source trees while retaining
most of the RSS savings from the earlier ZIP-on-disk implementation.
Source: [same-runner A/B workflow 41135533769](https://github.com/iivkis/rgp/actions/runs/41135533769)
on [PR #41](https://github.com/iivkis/rgp/pull/41), comparing separately
compiled baseline and candidate binaries against the **same pinned Git
commit** for each of five profiles.

The candidate generated **exactly the same RGP bytes** as the baseline
in all five cases. All actual outputs passed Rust verifier, Python
Reader and pinned Git blob equality checks. The three independently
pinned external public-repository RGP outputs also stayed byte-identical.
This is single-run CI evidence, **not** a statistical production benchmark.

## Same-runner measured generator RSS and latency

| Git profile | Old elapsed ms | New elapsed ms | Old peak RSS KiB | New peak RSS KiB | Peak RSS change |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2,000 × 1KiB files | 320 | 200 | 16,720 | 19,388 | +15.96% |
| 12 MiB incompressible | 470 | 470 | 51,912 | 64,288 | +23.84% |
| 12 MiB highly compressible | 190 | 190 | 51,880 | 64,192 | +23.73% |
| 36 MiB incompressible, forced disk fallback | 2,000 | 1,450 | 159,180 | 159,224 | +0.03% |
| 50,005 distinct Git files (50k ZIP member cap) | **4,370** | **2,130** | **90,596** | **105,680** | **+16.65%** |

The 50k scenario improved from 4.37 to 2.13 seconds
(**approximately 2.05× throughput in this one run**) at the cost of
a 16.65% higher peak generator RSS (88.5 to 103.2 MiB).
This remains far below the roughly 183 MiB peak generator RSS measured
for the original pre-streaming implementation on its own workload,
though these are from different runs and must not be compared as a
controlled three-version A/B.

The 36 MiB forced-disk scenario confirms the fallback still works and
improved from 2.00 to 1.45 seconds without a meaningful RSS change.
All five jobs' actual timings are full CLI generator measurements and
exclude fixture creation, network connector, inference tokens and
independent natural-language answer quality.

## How it works

- An estimated ZIP footprint includes uncompressed Git blobs,
  serialized manifest and **256 bytes per ZIP entry** for headers and
  compression overhead. If this conservative estimate is at most
  **32 MiB**, the ZIP uses a seekable in-memory `Cursor<Vec<u8>>`,
  removing high-cardinality ZIP header file seeks.
- Otherwise, ZIP creation uses a **256 KiB `BufWriter<File>`** on a
  uniquely created temporary disk file.
- Both paths inspect real ZIP compression metadata, preserve the
  deterministic anti-zip-bomb `ZIP_STORED` fallback, and feed a
  shared generic `Read + Seek` Base64 writer.
- Base64 is still streamed in 57-byte input / 76-character output
  chunks through the existing 64 KiB output buffer. A complete
  encoded transport string is **never** accumulated in memory.
- RGP is independently verified **before** the atomic final rename.
  Unique file staging and Drop cleanup remain in place.
- No new Python or Rust dependency, no schema change, no wire format
  change, and no decrease in the existing verifier resource limits.

The 32 MiB figure is a **conservative selection estimate**, not a
strict process allocation cap. `Vec` capacity, ZIP metadata,
the source blob map and independent verifier still use more memory.
No constant-space claim is made. The disk fallback has its own
legitimate 36 MiB stress case, rather than being unreachable dead code.

## Exact output identities

| Profile | Actual RGP SHA-256 |
| --- | --- |
| 2k files | `7c703a5fdb5d76abd7c547109db670e3c643e620efc6104f8eea1858135e403b` |
| 12MiB incompressible | `3bd1e90f3d09166c356b9e66f755735450277b060e358b9763a05654fa326dff` |
| 12MiB compressible | `4987077907d9ab82a3880fade2e96cca9e186bb72b0b3389a4d74f9dd4b9e9e9` |
| 36MiB forced disk | `8847e11778df61946018c6b0713256bed1f174f8ddb4cd22afd95fcb9baf1572` |
| 50k ZIP entries | `183b3b11c22e7310a40521b8caa0f704a2fe0cef1bd28c35d9685cde682b44d4` |

The fixed 50,005-file synthetic Git commit still yields 49,999
included source paths plus 6 explicit `entry_limit` exclusions;
the snapshot remains correctly `partial`.

## Unmeasured and unresolved

- Five single runs on an ephemeral GitHub Actions host, no confidence
  intervals, no disk-I/O instrumentation. Large ZIP compression
  characteristics and disk performance vary across repositories.
- The 32 MiB estimate is not an allocator limit.
- Peak memory tradeoff is positive: candidate uses 16–24% more RAM
  on three profiles. Do **not** call it both faster and leaner than
  the previous disk-backed generator.
- No GitHub connector end-to-end timing, tokens, user-perceived
  latency, production performance or independent answer-quality
  metric measured here.
- **Gate B = HOLD**, independently pending 24 independently graded
  answer pairs and verification of 19 original provider-call events.

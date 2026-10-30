# Stage 1: three-repository pinned corpus diagnostic

**Status:** Stage 1 functional acceptance extension, **not** Gate B GO.
The canonical plan remains [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md).

## What this run actually tests

The dedicated [GitHub Actions matrix](../.github/workflows/stage1-corpus.yml)
runs on `rgp`, `serde_json` and `itsdangerous`, using the **exact
repository commits and 12 task/reference-path sets already frozen** in
[benchmarks/gate_b/plan.json](../benchmarks/gate_b/plan.json).
The script [stage1_corpus.py](../scripts/stage1_corpus.py) runs as a
developer-side offline comparison **after downloading** these public
repositories through normal Git. The RGP that is tested is built by
the current PR's Rust generator, not reused from earlier results.

Each matrix job:

1. Validates the frozen Gate B plan and hardcoded source anchors.
   Rejects task/path drift instead of silently dropping a case.
2. Fetches exactly one approved pinned Git commit. Does not execute code
   from any external repository and does not run LLMs or external API models.
3. Generates an RGP twice from that commit, compares all output bytes,
   verifies it with the Rust verifier, opens it in the Python stdlib Reader,
   checks snapshot completeness and traverses its entire paginated tree.
4. For **every preregistered task reference path**, reads the real Git
   blob from the pinned commit, matches it byte-for-byte against the
   verified RGP blob, performs fixed lexical Reader `search`, checks
   returned exact line numbers and `read` content against the Git original.
   Duplicate reference files across tasks are fetched once from Git.
5. Confirms truncation, unsupported format versions and modified transport
   SHA-256 are rejected, rather than returning plausible source data.
6. Collects actual `/usr/bin/time` elapsed wall time and maximum RSS
   for both Rust generation passes and verification; Reader initialization
   and navigation wall time; process-high-water RSS of the Python
   measurement process.

The report includes pinned source commit, RGP SHA-256 and size,
result for each of the 12 original **source-navigation anchors**, resource
figures, verified file count and explicit missing metrics.
It **never publishes raw repository source or the RGP**.

### Commands

```sh
# One repository, from a machine/CI runner with network, git, Rust and GNU time:
task stage1:corpus REPOSITORY=rgp
task stage1:corpus REPOSITORY=serde_json
task stage1:corpus REPOSITORY=itsdangerous

# Or run the GitHub Actions workflow "Stage 1 pinned-corpus evidence".
```

The self-contained tests for the protocol run without network:

```sh
python3 -m unittest discover -s tests -p 'test_stage1_corpus.py' -v
```

## Metrics and limitations

- Generation `elapsed_ms` includes Rust binary execution and Git blob
  reads, **not the public repository checkout**. Git transport time is not
  in this metric.
- `peak_rss_kib` from GNU time is the maximum resident memory of
  the Rust command, on the particular ephemeral Ubuntu runner.
- Python process `ru_maxrss` is a **whole-process high-water mark**,
  not precisely incremental Reader memory. Both Reader and task
  wall times are observed, but end-to-end GitHub connector latency,
  transfer cost and Python setup are **not** measured.
- Matrix runners are independent and their resource results cannot be
  used as a controlled before/after speedup without matched baseline runs,
  repetitions and platform controls.
- The source anchors demonstrate **lexical navigation and byte correctness**,
  not the independent natural-language assessment of all Gate B rubrics.
  They do not measure correctness of agent explanations, token savings
  or real connected-GitHub call counts.

## Evidence and decision rules

An individual corpus job is `PASS` **only** when deterministic RGP
generation, Rust/Python verification, all reference blobs, exact line
answers and negative cases pass. A source mismatch, failed fetch,
resource limit or unavailable tool produces `FAIL`, with the reason
recorded. Green CI alone is insufficient: inspect the JSON artifacts
`stage1-corpus-rgp`, `stage1-corpus-serde_json` and
`stage1-corpus-itsdangerous` from the identified workflow run.

**Gate B remains HOLD.** This diagnostic is a prerequisite of a reliable
product, not a model-based two-arm experiment or independent review.
The 12 tasks still need answer-level evidence and authenticated
call-history review under the existing Gate B protocol.

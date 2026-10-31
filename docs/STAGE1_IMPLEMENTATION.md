# Stage 1 implementation: reliability and agent-owned acceptance

> **Исторический материал:** этот отчёт описывает более ранние
> форматы и/или измерения; для текущего RGP/3.0 см.
> [канонический roadmap](PRODUCT_ROADMAP.md) и
> [актуальное техническое ревью](REVIEW_2026-10-31.md).
> Его ZIP/JSON/внутренние SHA выводы не переносятся на RGP/3.0
> без повторного измерения и внешней проверки по Git.


**Scope:** first large implementation slice of [Stage 1](PRODUCT_ROADMAP.md).
**Status:** in progress; **Gate B remains HOLD**. This document describes
the implementation and checks to reproduce, not a declaration of Stage 1 GO.

## Delivered in this slice

1. **Git blob retrieval:** the Rust generator now uses a single
   `git cat-file --batch` child and reads each verified object before
   requesting the next. This removes per-blob Git subprocess startup,
   without introducing a new dependency or changing the RGP 1.0 format.
2. **Concurrent generation:** `generate` creates a unique same-directory
   temporary file using exclusive creation, verifies it before publication
   and removes staged files after failure. Concurrent generators cannot
   overwrite one another's intermediate data. The final destination is
   still last-writer-wins, with reproducible identical content at the
   same pinned commit.
3. **Reader memory:** Python still verifies *all* hashes and resource
   limits on construction, but keeps compressed ZIP bytes and a set of
   verified blob identifiers, **not the complete uncompressed repository**
   alongside the archive. Reads/search decompress only requested blobs.
   This is not streaming the outer Base64 transport and does not imply
   support for arbitrarily large repositories.
4. **Goal-level RGP test:** `scripts/stage1_acceptance.py` generates the
   same pinned repository twice, verifies bytes with Rust/Python, reads
   *the produced RGP* to answer three fixed code-navigation tasks
   (Rust generator, Python Reader, product roadmap), compares full source
   bytes and source line numbers against Git at that exact revision,
   rejects corrupted inputs, exercises tree pagination and confirms three
   concurrent generators can publish the same verified result.
5. **CI evidence:** each PR CI run executes this acceptance after building
   `target/debug/rgp`, reports `PASS/FAIL` and publishes
   `stage1-acceptance.json` with SHA-256, source commit, source hashes,
   checked line numbers and failure reasons; source bytes are not uploaded.

## Reproduce

```sh
cargo build --locked
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/golden_fixtures.py check --rust-cli target/debug/rgp
python3 scripts/stage1_acceptance.py --rust-cli target/debug/rgp \
    --report /tmp/stage1-acceptance.json
# Or, with Task installed:
task check
```

The acceptance script is a developer/agent-side test, not a model runner.
It assumes a full local Git checkout for the independently authenticated
source baseline. There are no external network or model calls. Its summary
does not include proprietary source contents and must not be interpreted as
a public benchmark result.

## Explicit remaining Stage 1 gates

- **Gate B remains HOLD:** 12 frozen source tasks need appropriate
  independently graded evidence and authenticated call-history audit;
  this three-task regression is *not* a substitute.
- Larger pinned corpora with measured build latency, peak RSS, Reader
  overhead, upper limits and connector-specific delivery must be tested
  before asserting production-scale improvements.
- Format/schema/transport compatibility and adversarial resource limits
  still require a product acceptance matrix and any necessary hardening.
- GitHub Actions evidence is only as fresh and trustworthy as its
  identified run, revision and logs. A successful local test alone does
  not prove transport authenticity through the connected GitHub channel.

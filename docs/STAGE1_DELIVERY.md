# Stage 1: verified Actions delivery and source-answer proof

This is the Stage 1 delivery counterpart to the
[pinned-corpus functional test](STAGE1_CORPUS_DIAGNOSTIC.md) and the
[resource stress test](STAGE1_STRESS.md).

**Goal:** make the existing GitHub Actions ZIP delivery path reproducibly
verifiable against a trusted reference *outside the downloaded ZIP*.

## The actual trust boundaries

1. The frozen three-repository task plan lives in
   [plan.json](../benchmarks/gate_b/plan.json). The expected RGP SHA-256,
   source commit, size and included-file count come from the independently
   committed [corpus checkpoint](../benchmarks/gate_b/corpus_preparation.json).
   Consumers must authenticate the repository/commit providing that
   checkpoint; downloading a SHA-256 from the same untrusted artifact is
   not an independent anchor.
2. The `Gate B pinned corpus preparation` workflow checks out an exact,
   pinned public Git commit and produces a real `REPO_CONTEXT.rgp`. It
   runs the Rust verifier, Python Reader and a real
   Actions-style ZIP bootstrap with the **committed** expected checksum.
   Then it independently compares every frozen task reference blob,
   Reader `read` and lexical `search` answer to the pinned Git source.
3. The workflow publishes **two distinct artifacts per repository**:
   `gate-b-<repo>` (actual `.rgp` + co-packed provenance) and
   `stage1-delivery-<repo>` (metadata-only test evidence). The former
   intentionally contains public code snapshots from the 3 approved
   public repositories; do not reuse this workflow for private source
   without separately reviewing storage and access permissions.
4. A connected agent retrieves the **actual** `gate-b-<repo>` artifact
   ZIP through the selected GitHub app and confirms its workflow run,
   repository and artifact ID in the authenticated tool response.
   The agent then re-runs the exact `stage1_delivery.py` verifier
   on the received ZIP against the **separately retrieved** trusted
   checkpoint. It independently inspects the resulting RGP through
   Reader, compares the task evidence with pinned source when Git
   is available, and records which connector calls actually occurred.

## Reproduce the consumer side

For each repo `rgp`, `serde_json` and `itsdangerous`:

```sh
python3 scripts/stage1_delivery.py \
  --actions-zip /path/to/downloaded-github-actions-artifact.zip \
  --repository rgp \
  --output /tmp/rgp-pinned.rgp \
  --report /tmp/rgp-delivery.json

# When an independently checked pinned Git clone is available:
python3 scripts/stage1_delivery.py \
  --actions-zip /path/to/downloaded-github-actions-artifact.zip \
  --repository rgp \
  --source-repo /path/to/pinned-git-clone \
  --output /tmp/rgp-pinned.rgp \
  --report /tmp/rgp-delivery.json
```

`--source-repo` enables the independent Git-byte and read/search checks
for all four planned task reference sets for that repository. Without
that option the script checks *the complete, SHA-pinned RGP* and all
reference paths and reports `source_comparison: NOT_RUN`; it never
pretends it independently fetched Git source.

The test script **does not claim connector authentication** simply
because somebody passes it a file path, even when all local bytes match.
The `connector_acquisition` field is always
`NOT_ATTESTED_BY_LOCAL_SCRIPT`. The agent must provide separately
observable evidence from the actual GitHub plugin call and run metadata
before describing the ZIP as downloaded via that channel.

## Rejection conditions

- Wrong RGP SHA-256 (even if co-packed provenance agrees).
- Wrong pinned commit, partial snapshot, missing paths or incorrect
  member count; duplicated/unsafe/extra ZIP members; incompatible
  manifest and malformed provenance.
- Contradictory or missing frozen checkpoint entries.
- Source-content, line-coordinate, or Reader-read mismatches when
  `--source-repo` is provided.

The validator writes a `FAIL` JSON record on error and must exit
nonzero. The Rust generator, independent Python Reader and ZIP
bootstrap stay separate; there is no new inference runtime or API
dependency.

## Gate B cannot be auto-approved

A successful ZIP replay and a SHA-pinned connector download establish
**specific delivery/byte-integrity evidence only**. They do not grade
the 24 assistant-written answers independently, prove actual historical
GitHub source-fetch counts, or measure inference tokens. Existing
unblinded results remain a diagnostic; the official
[Gate B review](GATE_B_REVIEW.md) remains **HOLD** until independent
answer-level source evaluation and authenticated call-history audit
are supplied. A CI job never changes this verdict to GO.

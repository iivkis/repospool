# Stage 1: actual connected GitHub ZIP delivery audit (31 Oct 2026)

**Verified run:** [Gate B pinned corpus preparation 41130695334](https://github.com/iivkis/rgp/actions/runs/41130695334),
triggered by [PR #37](https://github.com/iivkis/rgp/pull/37).
Its three jobs finished **SUCCESS**. Each job independently checked a
`REPO_CONTEXT.rgp` from a pinned public Git commit using the **committed**
expected SHA-256 in `benchmarks/gate_b/corpus_preparation.json`,
the Rust verifier, the Python Reader, a locally constructed Actions-style
ZIP bootstrap, and the frozen Git source tasks. The full jobs uploaded
actual `gate-b-<repo>` Actions artifact ZIPs.

## Actual GitHub connector downloads in this conversation

The developing agent then used the **connected GitHub artifact download
action**, not the CI runner's local ZIP, to retrieve the three uploaded
archives. GitHub provided distinct artifact IDs; the downloaded ZIPs were
opened independently in the agent's Python environment. The agent checked
the canonical RGP transport header, Base64 payload SHA and declared size,
all manifest/ZIP blob SHA-256 values and input tree digest, as well as the
pinned source commit and original Git blob SHA-1 values from **separate
GitHub source retrievals**.

| Repository | GitHub artifact ID | ZIP bytes | RGP bytes | SHA-256 of the RGP | Independent Git blobs checked |
| --- | ---: | ---: | ---: | --- | ---: |
| rgp | 13012862024 | 98,238 | 132,217 | `8e5871336d1aaad32d89e6e1b0e581a836d608e7e3e453b10c32b6d586fcaa6f` | 5 |
| serde_json | 13013097179 | 192,270 | 260,958 | `7802be3b67bfab094518c76f87f0baec749f4a40dd4458dd14fcc7f3de588a6a` | 4 |
| itsdangerous | 13012936765 | 106,346 | 143,569 | `c4f60f1b6231df092d60df440f7b8e7f6bcf3d8c9af6672fee656110f5183446` | 4 |

**Observed:** 3/3 connected artifact retrievals passed all transport,
hash, manifest, blob and tree-digest checks; 13/13 independently
retrieved pinned Git file identifiers matched the embedded source bytes.
22 predefined lexical source anchors were present at valid line
positions. All three manifests reported complete snapshots and
honest `not_indexed` semantic coverage. No fetched source code was
executed.

The separate **12-task functional acceptance** of the actual generated
RGP with source `read/search` against pinned Git was performed inside
the CI workflow, and all three jobs passed. The connected-download audit
checked the received bytes against those same committed artifact
digests and independent Git blob identities.

The records above are based on the GitHub tool responses and actual
downloaded bytes observed in the same development chat. They are not a
cryptographically signed execution transcript and do not independently
authenticate the original 29 Oct assistant's historical source-call
ledger. The independently stored SHA anchor is a **Git repository trust
anchor**: if the pinned checkpoint/repository were compromised, or
the workflow's producer environment were untrusted, matching SHA-256
would not independently prove publication provenance. The GitHub
connection and pinned source identities must themselves be trusted.

## Gate B decision: HOLD

The following remain **not yet established**:
- an external, independent reviewer graded the 24 A/B answers against
  the pinned rubrics and source ranges;
- authenticated reconstruction of the **original** GitHub fetch and
  RGP acquisition calls in the Gate B comparison (the calls in this new
  delivery audit are separate and cannot be substituted retroactively);
- controlled repeated comparisons that permit a public performance
  claim, together with measured tokens and end-to-end user latency.

The actual ZIP delivery path now has positive, source-grounded evidence.
That is a major Stage 1 milestone, **not** an official Gate B GO.

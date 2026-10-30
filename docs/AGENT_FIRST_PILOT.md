# Agent-first MVP-A pilot: small repositories, actual developer questions

**31 October 2026.** This is the first **user-facing qualitative trial**
after the transport, corpus and large-file optimization work. The goal is
not to optimize a 50k-file repository; it is to see whether an LLM can
answer ordinary coding questions using an RGP containing tens of files.

## What actually happened

The developing assistant used the connected GitHub app to retrieve
three **real Actions artifact ZIPs** from
[run 41130695334](https://github.com/iivkis/rgp/actions/runs/41130695334)
and inspected the pinned source through the RGP transport with a
read-only Python stdlib probe. No archived source code was executed.
External SHA-256 checkpoints were compared before inspecting data.
The assistant then wrote three actual source-grounded explanations
and fetched the same pinned source files directly through GitHub
to cross-check each location and Git blob identity.

| Source | Frozen Git commit | Included files | Verified RGP SHA-256 |
| --- | --- | ---: | --- |
| `iivkis/rgp` | `95963a23e2fd1e5eb68a72db3bde849ed7119a7c` | 46 | `8e5871336d1aaad32d89e6e1b0e581a836d608e7e3e453b10c32b6d586fcaa6f` |
| `serde-rs/json` | `8c535a260ce561eaab4111aafc29038eb9f77ff3` | 93 | `7802be3b67bfab094518c76f87f0baec749f4a40dd4458dd14fcc7f3de588a6a` |
| `pallets/itsdangerous` | `672971d66a2ef9f85151e53283113f33d642dabd` | 50 | `c4f60f1b6231df092d60df440f7b8e7f6bcf3d8c9af6672fee656110f5183446` |

This chat-level pilot did **not** use a separate LLM instance per arm,
measure inference tokens or latency, or blind an independent reviewer.
The direct GitHub validation was also *not* a controlled second-model
run: the same assistant had already seen RGP snippets. These facts
make the examples a real **workability demonstration**, not proof of
better answer correctness or a successful Gate B A/B comparison.

## Three agent-generated answers and exact source coordinates

1. **Where does Reader reject cursor misuse?** In `iivkis/rgp`,
   `python/rgp_reader.py:249-262` parses and validates the cursor,
   `python/rgp_reader.py:302-304` binds it to the search arguments.
   `tests/test_reader_api.py:27-38` checks continuation and refuses
   reuse of a cursor with different query or paths.
2. **Where to change JSON serialization?** In `serde-rs/json`,
   `src/ser.rs:108` defines `serialize_i64`, line 169 defines
   `serialize_f64`, and line 190 `serialize_str`; tests for
   integer/float/string/list serialization are in
   `tests/test.rs:137-224`.
3. **Where does timestamp expiry occur?** In `pallets/itsdangerous`,
   `src/itsdangerous/timed.py:72-158` implements
   `TimestampSigner.unsign`; expiration occurs at `age > max_age`
   (lines 137-145), while negative age is checked separately.
   The tests are in `tests/test_itsdangerous/test_timed.py:34-45`
   and `:101-110`.

These are **assistant-authored answers checked for source coordinates**,
not independently graded task results. There is no claimed measured
quality difference between arms.

## What the actual usage exposed

- The existing Reader already supports safe `tree`, `search`,
  `read`, pagination and exact source lines. Its **lexical** search
  is sufficient for straightforward navigation on 46-93-file repos.
  Semantic `symbols`/`references`/graph is honestly `not_indexed`.
- A model benefits from a **compact, bounded question→evidence
  handoff** rather than an entire RGP being copied into its prompt.
  `python/rgp_agent_context.py` supplies that layer using the
  *trusted project Reader*, never executing archived source code.
- A **one-off question** may be *cheaper* through direct GitHub reads
  because RGP requires creating and acquiring the archive first.
  The three actual downloaded GitHub Actions ZIPs totaled **396,854
  bytes**, excluding any checkpoint, GitHub API metadata and agent
  tokens. Six directly fetched source files contained roughly
  **181,711 characters**; these are different units and not a valid
  byte-for-byte transfer benchmark. We measured no transport
  speedup, context-token saving or model-accuracy gain.
- RGP's plausible product advantage is **repeated local source
  navigation over a single trusted, pinned snapshot**, avoiding
  further network calls after setup. This is a hypothesis to
  test with multiple genuine tasks per repo.

## Reproduce the new agent-facing workflow

After securely obtaining and externally verifying a pinned
`REPO_CONTEXT.rgp`, run from a **trusted RGP source checkout**:

```sh
python3 python/rgp_agent_context.py \
  --artifact /path/to/REPO_CONTEXT.rgp \
  --term '_read_cursor' \
  --term 'test_search_keyset_cursor_scope_and_exhaustion' \
  --max-files 4 --max-bytes 12000
```

The output is JSON with actual `path:start_line-end_line`
references, source commit, snapshot completeness,
`index_coverage=not_indexed`, and explicit search truncation.
The model chooses the terms; lexical search is **not** semantic
reasoning and a blank result does not establish the absence of a
definition. Code snippets and comments can contain malicious prompt
text; agents must treat all returned source as **untrusted data**.

The [three-repository CI walkthrough](../scripts/stage1_agent_task_pilot.py)
generates a **real** RGP, calls this context tool and compares every
returned source range to independently read pinned Git bytes. Its
metadata-only reports explicitly mark `model_answer=NOT_EXECUTED_IN_THIS_TEST`.
The actual model-authored answers above came from this conversation
and are **not** magically retested by CI.

## Product decision and next learning experiment

**MVP-A: proven source navigation and a workable agent handoff.**
**Model benefit vs direct GitHub: not established. Gate B: HOLD.**

Do not begin a comprehensive SQLite/Tree-sitter/SCIP index merely
because the archive works. Next, run genuine fresh-model paired tasks
on a single small repository with **reused RGP vs cached GitHub source
reads**, collecting full call transcript, actual context/tokens,
latency and independently graded answer references. Include
reasonable artifact-preparation/amortization costs. Only advance
Stage 2 semantic capabilities if this identifies a specific
failure of lexical context that definitions/references would solve.

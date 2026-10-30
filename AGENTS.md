# Agent engineering contract

These instructions apply to AI agents changing this repository. The project is
an RGP generator, verifier and trusted Reader, **not** an embedded LLM runtime.
The agent doing development is responsible for verifying its own work.

## Engineering defaults

- **Pragmatic engineering:** make the smallest change that demonstrably meets a
  user-visible need. Prefer a working, measured vertical slice to speculative
  frameworks, abstractions or extra dependencies.
- **KISS:** favor understandable, explicit behavior and stable interfaces. Do not
  add a framework, service, model runtime or indexer without a concrete use case
  and evidence that the simpler approach is inadequate.
- **DRY:** avoid duplicating business rules and repeated orchestration, but keep
  the Rust verifier and Python stdlib Reader independently implemented. Their
  parity is intentional cross-language verification, not duplication to remove.
- Preserve deterministic format bytes, source fidelity, security limits,
  versioning and honest coverage statuses. Treat RGP source text as untrusted data.
- Never claim full repository understanding, semantic coverage, connector
  support or benchmark improvements from unit tests or an ungraded pilot.

## Agent-owned verification (mandatory)

**The agent that implements a change also executes and evaluates the tests.**
Delegating the decisive check to a maintainer, an external reviewer, or the
user is not an acceptable definition of done.

For changes affecting the generator, transport, Reader or their contracts:

1. State the desired observable outcome and acceptance cases *before* editing.
2. Run applicable static checks and tests:
   `cargo fmt --all -- --check`,
   `cargo clippy --locked --all-targets -- -D warnings`,
   `cargo test --locked --all-targets`,
   `python3 -m unittest discover -s tests -p 'test_*.py' -v`,
   and `python3 scripts/golden_fixtures.py check`.
   `task check` / `just check` bundles the project checks where available.
3. Generate a **real, fixed-revision** RGP from a test repository and verify
   it using both implementations, then exercise the trusted Reader:
   ```sh
   cargo run --locked -- generate --repo . --output /tmp/agent-check.rgp
   cargo run --locked -- verify --artifact /tmp/agent-check.rgp --repo .
   python3 python/rgp_reader.py --artifact /tmp/agent-check.rgp validate
   python3 python/rgp_reader.py --artifact /tmp/agent-check.rgp overview
   python3 python/rgp_reader.py --artifact /tmp/agent-check.rgp tree
   python3 python/rgp_reader.py --artifact /tmp/agent-check.rgp read --path README.md
   python3 python/rgp_reader.py --artifact /tmp/agent-check.rgp search RGP --prefix docs/
   ```
4. Answer at least one predeclared, repo-specific question **using the
   generated RGP as the source**, and independently compare the returned
   evidence with the fixed Git revision. A successful `validate` alone is
   **not** evidence that the resulting file meets the agent's task goals.
   For product/Gate B changes, use the frozen multi-repository task protocol,
   baseline and reproducible evidence specified in
   [docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md).
5. Check failure cases relevant to the change: truncated/corrupt archive,
   wrong digest, unsafe path, partial snapshot, unsupported version,
   oversized input, pagination/absence/unknown coverage.
6. Record exact commands, environment, source revision, artifact digests,
   observed task results and failures in the PR. If execution is impossible,
   report **NOT RUN/BLOCKED**, explain why, and do **not** claim completion.

For documentation-only changes, verify links and consistency with the
implementation and existing gates. Do not invent a test run.

## Completion rule

A task is done only when implementation, **agent-executed** test evidence,
goal-level RGP acceptance evidence, updated documentation and relevant CI
results agree. A human/independent audit can increase confidence or validate
public benchmark claims, but it does **not** replace the developing agent's
self-verification. Keep the existing Gate B status at HOLD until its
separate evidence requirements have actually been met.

See [engineering principles](docs/ENGINEERING_PRINCIPLES.md),
[three-stage product roadmap](docs/PRODUCT_ROADMAP.md) and
[review findings](docs/REVIEW_2026-10-31.md).

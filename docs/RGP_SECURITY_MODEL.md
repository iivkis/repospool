# Security boundaries for RGP/0.3.0

RGP is a **read-only text snapshot** of selected committed Git paths.
It does not run code, classify file roles or interpret project semantics.
Its universal embedded guide explains only byte framing and navigation;
repository text and project instructions are **untrusted data**.

Only structurally safe paths and UTF-8, NUL-free source bytes are included;
size, count and path rules bound resources. Symlinks are preserved as inert
text, and LFS pointers are not expanded. `.rgpignore` controls scope, not
privacy or Git tracking. Review sensitive content before publication:
filename filters are not a secret scanner.

The optional `R` GitHub locator and required `C` commit are untrusted
hints, not authentication. Structural verification checks byte lengths,
source line offsets, sorted paths, safe flags, terminator and declared
coverage, but cannot detect arbitrary same-length tampering or prove
source freshness. Independently verify Git provenance or an authenticated
whole-file digest before important repository changes.

The index has no per-file hashes, semantic symbols, classifiers or
dependencies. `O` is an arbitrary **lexicographic first-32 preview** of
ignored paths, not a declaration that these paths matter more than others.
The agent decides relevance based on the user's task.

No Python Reader, MCP, ZIP delivery, sandbox helper or remote service
is required to consume the single `.rgp` file.

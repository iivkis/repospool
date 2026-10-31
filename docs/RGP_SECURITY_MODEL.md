# RGP/0.2.0 security model

The single `.rgp` file is **untrusted source context**, not executable
instructions or a Git checkout. A generated guide describes only transport.
Project files and project-specific `AGENTS.md` remain ordinary data.

A small source locator `R github:owner/repo` (when Git remote is recognized)
and one Git revision `C <commit>` help agents target GitHub requests. These
fields are not authenticated. **Never trust them to authorize writes without
independent GitHub identity and revision checks.** No file-level hashes or
whole-file digest are written into RGP; local Rust verification may compute
temporary hashes only in process memory.

`F` records supply exact source byte length, first RGP source line and source
line count; verifiers reject inconsistent offsets. `D` records are bounded
lexical symbol hints, not parser-verified semantics or dependencies. They can
be incomplete and must not substitute for reading actual code.

Generator reads committed Git data (not unstaged edits), rejects invalid
paths and non-UTF-8/binary sources, bounds file and artifact sizes, and never
dereferences symlink targets. `.rgpignore` intentionally limits coverage;
the index discloses selected important omitted paths (O). Before sharing,
review confidentiality because filename-based filtering is not a secret
scanner.

The agent needs no Python, MCP or sandbox adapter. Verify provenance
separately using GitHub or an independently trusted whole-file digest. A
structurally valid file alone cannot detect equal-length tampering or prove
freshness. The `verify --repo` CLI cross-checks included source bytes against
the specified repository's current HEAD; it is not an external trust anchor.

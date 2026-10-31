# RGP product roadmap

**Goal:** `rgp generate .` using an installable Nix flake, producing one
source-context artifact usable directly by an agent.

## RGP/0.2.0
- Minimal repository locator (optional) and one Git commit ID, not per-file hashes
- Exact RGP line coordinates for source sections and corresponding Git source lines
- Bounded lexical symbol outline (Rust, Python, Go, JavaScript, TypeScript)
- Important excluded-path notices, including omitted CI and lock files
- Versioned deterministic Rust generator and structural verifier
- No Python Reader, ZIP, MCP, database, semantic graph or remote runtime

## Next validation
Compare GitHub-only, original 0.1.0 and navigable 0.2.0 on the same
source-editing tasks. Measure correct source selection, connector calls,
tokens, time and successful verified edits. A file format improvement alone
is not an established assistant quality gain. Gate B remains HOLD pending
independent evaluations.

Source locator and revision are hints; the agent must verify provenance
and GitHub permissions independently before modifying a live repository.

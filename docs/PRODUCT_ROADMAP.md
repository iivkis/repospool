# RGP product roadmap

**Target:** publish an installable Nix flake providing the Rust command
`rgp generate .`. A user can add `github:iivkis/rgp` as a flake input and
receive one `REPO_CONTEXT.rgp` file to hand directly to a coding agent.

## Product boundary

- Generator: Rust binary with deterministic, versioned text output.
- Distribution: Nix package and runnable app, usable from another flake.
- Artifact: universal guide, file index and original UTF-8 source bodies.
- Consumer: coding agent reads **one file**. No Python scripts, MCP server,
  Reader installation, ZIP delivery or extra local workspace required.
- Provenance: independently trusted Git revision and artifact digest remain
  external; the text is read-only source context, not a trust anchor.

## Acceptance

1. `nix build .#rgp` installs the binary, including access to Git at runtime.
2. `rgp generate .` creates `REPO_CONTEXT.rgp` in the target repository.
3. Running twice on unchanged committed source produces identical bytes.
4. Rust validates the output and its source boundaries.
5. A coding agent can navigate the artifact's header, index and FILE sections
   with ordinary file-reading capabilities, without any companion scripts.
6. Evaluate source-understanding tasks against a GitHub-only baseline.
   **Gate B remains HOLD** until independent task results justify a change.

Older ZIP, Python Reader, MCP, sandbox and performance experiments are
historical; their scripts are removed from this product's runtime and CI.

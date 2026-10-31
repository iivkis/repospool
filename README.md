# RGP

**RGP is a deterministic source container, not a code interpreter.** It makes
any Git repository easy for an agent to inspect. The agent, not the generator,
decides what the code means, what matters and how to change it.

```sh
rgp generate .
# creates ./REPO_CONTEXT.rgp
```

The single UTF-8 `RGP/0.3.0` file has a fixed reading guide, sorted file
index, exact byte boundaries, line coordinates, a bounded coverage preview
and original committed source. It contains **no language detection, symbol
extraction, dependency analysis, file-role guesses, semantic summaries, or
per-file hashes**. No Reader, Python scripts, MCP server or sandbox adapter
is needed to consume it.

## Nix installation

Add `rgp.url = "github:iivkis/rgp";` to flake inputs and
`rgp.packages.${system}.default` to your dev shell, or run:

```sh
nix run github:iivkis/rgp -- generate .
```

Scope is selected by committed `.rgpignore`. Optional GitHub locator and one
Git commit ID are **untrusted navigation hints**, not proof of authenticity.
Verify provenance independently before making repository changes.

[Product contract](docs/PRODUCT_CONTRACT.md) ·
[Format](docs/RGP_FORMAT_SPEC.md) ·
[Security](docs/RGP_SECURITY_MODEL.md)

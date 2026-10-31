# RepoSpool

**Deterministic, line-addressable Git source snapshots for AI agents.**
RepoSpool packages selected committed UTF-8 source into one readable file.
The agent interprets code and architecture; RepoSpool never guesses semantics.

```sh
repospool generate .
# writes ./.repospool
repospool verify --artifact .repospool --repo .
```

The standalone `REPOSPOOL/1.0` text format contains a short generic reading
guide, sorted path/size/line index, explicit coverage limits and original source
bytes. There are no semantic indexes, generated summaries, sidecars, Reader,
MCP server, ZIP or Base64 layers.

## Reading from GitHub

1. Fetch the first lines of `.repospool` to inspect its compact file index.
2. Select relevant files using the task and project documentation.
3. Read only needed line ranges from `.repospool`, or fetch original GitHub
   source with the pinned commit. Index coordinates point into `.repospool`.
4. Before GitHub edits, independently check the repository, source commit and
   current branch; the embedded GitHub locator and commit are untrusted hints.

Optional `.repospoolignore` uses Git ignore rules from the pinned commit to
limit included files. Excluded paths remain available in GitHub. Maintainers
may write a few verified architecture constraints in README or AGENTS.md;
RepoSpool preserves that ordinary text without interpreting it.

## Nix

```sh
nix run github:iivkis/repospool -- generate .
```

For a flake input, use `repospool.url = "github:iivkis/repospool";`
and `repospool.packages.${system}.default` in a development shell.

[Product contract](docs/PRODUCT_CONTRACT.md) ·
[Format](docs/FORMAT.md) · [Security](docs/SECURITY.md)

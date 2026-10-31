# RGP

One command creates a **self-contained source map for coding agents**:

```sh
rgp generate .
# ./REPO_CONTEXT.rgp
```

RGP/0.2.0 includes the source text, a generic reading guide, GitHub locator
when available, **one** Git commit ID, precise file line positions, bounded
lexical symbols and important omission notices. **No per-file hashes or
companion scripts.** Agent reads the file directly, and independently checks
the GitHub source before making changes.

## Nix

Add `rgp.url = "github:iivkis/rgp";` to flake inputs and add
`rgp.packages.${system}.default` to your dev shell packages. Or run:

```sh
nix run github:iivkis/rgp -- generate .
```

Only committed UTF-8 Git data is packaged; `.rgpignore` limits scope.
Locator and commit ID are **untrusted pointers**, not authentication.

[Format](docs/RGP_FORMAT_SPEC.md) · [Security](docs/RGP_SECURITY_MODEL.md)

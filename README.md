# RGP

A single command generates a self-contained, read-only UTF-8 repository snapshot for a coding agent:

```sh
rgp generate .
# creates ./REPO_CONTEXT.rgp
```

## Use from another Nix flake

Add `rgp.url = "github:iivkis/rgp";` to `inputs`. In your `outputs`, add `rgp.packages.${pkgs.system}.default` to your development shell's `packages`. Then run `nix develop` followed by `rgp generate .`

Without changing a flake:

```sh
nix run github:iivkis/rgp -- generate .
```

The artifact includes a generated format guide, indexed paths and source bodies. The agent needs **only the .rgp file**, not a Python Reader, MCP or sandbox scripts.

Only tracked Git text is captured; `.rgpignore` can limit scope. RGP has **no built-in authenticity/freshness proof**: authenticate its origin separately before relying on it for source changes.

[Format](docs/RGP_FORMAT_SPEC.md) · [Security](docs/RGP_SECURITY_MODEL.md)

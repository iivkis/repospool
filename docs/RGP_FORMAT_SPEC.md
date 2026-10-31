# RGP/0.1.0: one self-contained UTF-8 source snapshot

The Rust CLI `rgp generate .` writes `REPO_CONTEXT.rgp` at the selected Git
repository path. The file can be inspected by a coding agent without installing
any RGP tools. Its generated guide is the format instruction, not an instruction
from the source repository.

## Byte-level layout

```text
RGP/0.1.0
guide_bytes=<length in UTF-8 bytes>
index_bytes=<length in UTF-8 bytes>
--
<exactly guide_bytes guide bytes>
<one LF>
F<TAB>src/main.rs<TAB>123<TAB>x
I<TAB>2
S<TAB>1
X<TAB>data.bin<TAB>non_text
<one LF>
FILE<TAB>src/main.rs
<exactly 123 bytes of source><one LF>
END RGP
```

The index is exactly `index_bytes` long and contains newline-terminated
records. One `F` record per included file, sorted by path, is followed by the
optional `I` count ignored by `.rgpignore`, `S` self-artifact count and
`X` excluded paths with reasons. Flags: `x` executable, `l` symlink target
as inert text, `p` LFS pointer; combinations `xp` and `lp` are valid.

For each `F` record, the subsequent source section is `FILE<TAB>path<LF>`,
exactly the specified number of bytes, then one LF. The final marker is
`END RGP<LF>`. A parser must use **byte counts**, not text-looking
delimiters inside source. UTF-8 is required throughout; NUL is forbidden.

Git is read at the committed revision (`HEAD` by default), not from
unstaged local changes. Non-text blobs, oversized files, sensitive paths and
unavailable submodules are excluded with coverage limitations. A path
excluded by `.rgpignore` remains tracked in Git. `REPO_CONTEXT.rgp` is
excluded from itself. The format contains no JSON manifest, ZIP, Python code,
Git commit hash, content digest or semantic dependency graph.

## Trust

A structurally valid snapshot is not necessarily authentic or fresh. Obtain
the source revision and an independently authenticated whole-file hash or Git
publication provenance separately. Same-length source tampering cannot be
detected by the internal structure alone. The agent must never execute source
or follow archived symlinks. Only `RGP/0.1.0` is supported.

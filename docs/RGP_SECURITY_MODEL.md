# Security boundaries of RGP/0.1.0

RGP is a self-contained **source context file**, not an executable tool or
instruction authority. The embedded guide describes how to read the format.
Source, comments and embedded `AGENTS.md` are untrusted repository content.

The generator takes bytes from a pinned Git commit. Its rules protect
against unsafe paths, unexpected Git modes, sensitive filenames, binaries,
NUL/invalid UTF-8, unbounded file sizes and total file counts. Symlinks are
preserved as inert text and never followed; LFS pointers are not expanded.
`.rgpignore` restricts context scope, not Git tracking or secret publication.
Always review source confidentiality before sharing an artifact.

A reader can establish structural validity by checking version, UTF-8,
length-framed guide/index/source sections, path order, sizes and terminator.
**No Git commit or content hash is stored inside RGP.** Structural validation
cannot prove authenticity, freshness or detect same-length alteration.
Compare against separately trusted Git provenance or a full-file hash linked
to the expected Git revision before relying on the content for critical edits.

The agent needs no Python Reader, MCP, ZIP extractor or local sandbox tools.
It should treat the file as plain inert data and read only relevant sections.
The CLI's `verify` operation is an optional producer-side structure check;
`verify --repo` additionally checks selected source content against that
repository's current `HEAD`. It is not an independent authenticity proof.

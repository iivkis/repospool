# RGP/0.3.0: universal byte-framed UTF-8 Git snapshot

RGP is a **mechanical transport**, not a semantic representation.
`rgp generate .` writes `REPO_CONTEXT.rgp` to the selected repository.
Only RGP/0.3.0 is supported. Earlier format versions are rejected.

## Byte-level layout

```text
RGP/0.3.0
guide_bytes=<UTF-8 byte count>
index_bytes=<UTF-8 byte count>
--
<exact guide bytes><LF>
R<TAB>github:owner/repo
C<TAB><one source Git commit ID>
F<TAB>path/to/source<TAB>123<TAB>42<TAB>5
I<TAB>4
S<TAB>1
O<TAB>ignored/file
X<TAB>other/file<TAB>non_text
<LF>
FILE<TAB>path/to/source<LF>
<exactly 123 source bytes><LF>
END RGP<LF>
```

This is **illustrative**; the index and FILE contents must agree exactly.
No classification, language tags, symbols, module roles or graph edges.

The first four lines are a fixed version, two ASCII decimal lengths and
`--`. The guide is exactly `guide_bytes` long, then one LF. The index
is exactly `index_bytes` long, then one LF. Every index record ends in
LF and uses tabs between fields. UTF-8 text must not contain NUL.

Index record order:

- `R`: optional sanitized `github:owner/repo` remote locator. It is a
  navigation hint, not proof of provenance.
- `C`: exactly one 40- or 64-character hexadecimal Git commit ID, mandatory.
- `F`: sorted included files, fields `path`, `source_byte_length`,
  `rgp_source_start_line` (1-based) and `source_line_count`, optionally
  flags. Flag `x` means executable, `l` inert symlink target and `p`
  LFS pointer, including combinations `xp` and `lp`.
- `I`: ignored file count; `S`: self-artifact exclusion count (when nonzero).
- `O`: first **at most 32** ignored paths in lexicographic order; partial,
  strictly mechanical preview of `I`, not an importance ranking. If `I`
  is larger than the preview, more ignored paths exist.
- `X`: sorted other excluded paths with explicit reasons.

Each `F` corresponds, in order, to `FILE<TAB>path<LF>`, exactly the indexed
number of original UTF-8 bytes, then one LF. The terminating marker is
`END RGP<LF>`. Determine file boundaries **only** from byte lengths.
No textual marker in original source changes framing. `rgp_source_start_line`
points to the first source-content line in the complete RGP; original source
lines start at 1 and ignore an empty final line caused only by trailing LF.

The index is descriptive, not authoritative about project purpose. A source
file with an unrecognized extension receives exactly the same treatment as
one written in a well-known language. The guide is fixed and generic.

## Provenance and security

Git input is the selected commit, not the working tree. `.rgpignore` and
size/non-text/safety rules may exclude files; excluded context is not silently
interpreted as irrelevant. A valid RGP is not necessarily authentic or
fresh. `R` and `C` must be cross-checked through trusted GitHub/Git sources
before live edits. No per-file or artifact hashes are embedded. The generator
and verifier may calculate private in-process fingerprints. Never execute
the archived source or follow its symlinks.

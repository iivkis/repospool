# RGP/0.1.0 transport specification

**Status:** experimental, single supported version. No backwards-compatible parser or writer.

## Layout

```text
RGP/0.1.0
guide_bytes=<decimal UTF-8 byte length>
index_bytes=<decimal UTF-8 byte length>
--
<exact guide_bytes bytes of deterministic, general-format guidance>
<LF>
F<TAB>src/example.py<TAB>120
F<TAB>scripts/run.sh<TAB>80<TAB>x
I<TAB>5
S<TAB>1
X<TAB>other/data.bin<TAB>non_text
<LF>
FILE<TAB>src/example.py
<120 original source bytes>
<LF>
FILE<TAB>scripts/run.sh
<80 original source bytes>
<LF>
END RGP
```

All sections are UTF-8; NUL is forbidden. Guide and index are length-framed in bytes, not characters. File bodies are framed by the index byte sizes; content resembling a FILE header or END RGP is not a separator. Paths and FILE sections use sorted path order. The format has an overall size ceiling of 192 MiB and limits individual files and total original content.

## Index

`F` specifies a path and exact size, optionally with flags `x` (executable), `l` (symlink text), `p` (Git LFS pointer), or `xp` / `lp`. `I` is the count excluded by `.rgpignore`; `S` counts the omitted RGP publication artifact; `X` gives a path and reason for other exclusions. `.rgpignore` itself, when present, is included as context. Ignored paths remain available from the original Git revision.

## General guide

The generator creates a deterministic guide from the transport contract in `src/lean.rs` and places it in every artifact. This text explains the format, index, selective reading, trust boundary and limitations. It contains no repository-specific name, paths, agent policy or provider-specific workflow. `AGENTS.md` may appear as a normal source file, never as the guide. The guide is untrusted data to the consuming assistant.

## Provenance and completeness

RGP/0.1.0 carries neither content hashes nor source Git commit IDs. A trusted Git publication revision and independently authenticated complete-file digest must bind the artifact to the intended source revision. Structural validation is not cryptographic authentication. A same-length edit can be structurally valid; compare source bytes against pinned Git where needed.

`snapshot_complete: true` means selected-scope source data is present. Files excluded intentionally by `.rgpignore` do not count as an incomplete selected scope; non-text, sensitive, submodules and other failures do. Source is not executed; symlinks stay inert text and LFS pointers remain pointers.

## Compatibility policy

Only `RGP/0.1.0` is accepted by the Rust verifier and Python Reader. All old ZIP/Base64 and plaintext 1.x/2.x/3.0 readers, writers and test fixtures are deliberately unsupported. The Rust crate version and format are both currently `0.1.0`; future compatibility is not promised until an explicit version policy exists.

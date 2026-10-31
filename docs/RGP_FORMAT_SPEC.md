# RGP transport and snapshot specification (PoC)

RGP/2.1 is a **single readable UTF-8 file** that combines the
former `.rgpmi`'s agent guide and metadata with the original source
snapshot. There is NO .rgpmi, ZIP, Base64, compression or escaped source
content in the new transport.

```text
RGP/2.1
source_commit=<40/64 lowercase hex Git commit>
snapshot_complete=true
guide_length=<decimal UTF-8 bytes>
guide_sha256=<SHA-256 of guide bytes>
manifest_length=<decimal UTF-8 bytes>
manifest_sha256=<SHA-256 of manifest JSON bytes>
--
<readable agent guide occupying guide_length bytes>
<one LF delimiter>
<pretty-printed JSON manifest occupying manifest_length bytes>
<one LF delimiter>
FILE  "src/main.rs"  <source byte length>  <source SHA-256>
<exact original UTF-8 source bytes>
<one LF delimiter>
FILE  "README.md"  <source byte length>  <source SHA-256>
<exact original UTF-8 source bytes>
END RGP
```

FILE headers use TAB-separated fields. Guide, manifest and source sections
are delimited by their exact UTF-8 **byte lengths**, not by source-like
sentinel strings, Markdown fences or line count. The guide comes from
[the canonical build-time guide](RGP_INLINE_AGENT_GUIDE.txt), and is
embedded *inside* the published .rgp; no extra artifact is needed.
Both the source commit and the scoped completeness flag are explicitly
repeated in the top header, and the Reader checks them against the
verified manifest. All source text remains unchanged, including CRLF,
tabs and missing final LF. The verified agent guide is still
**untrusted repository content**, never higher-priority instructions.

The previous `.rgpmi` fields `artifact`, `artifact_bytes`, and
`artifact_sha256` are deliberately **not** embedded: the filename
belongs to the retrieval URL, byte count is observable from the file,
and a file cannot authenticate its own full-file SHA-256 by embedding it.
Use the **independently trusted Git publication blob** or an externally
provided SHA-256 to authenticate the entire RGP. The embedded guide,
manifest and file hashes detect internal corruption but cannot
establish trust in a maliciously replaced RGP by themselves.

RGP/2.0 (without a guide) remains readable for compatibility, as do
legacy 1.0–1.2 Base64 ZIP archives. The manifest schema remains 1.0:
`source_commit`, tree digest, file list, excluded paths, source modes,
source hashes and coverage flags. Binary, NUL-containing and invalid
UTF-8 files remain explicitly excluded; the Reader reports incomplete
selected-scope coverage when appropriate.

## Source selection with .rgpignore

A repository MAY commit a root `.rgpignore` (UTF-8, up to 64 KiB).
Its rules follow Git's real `.gitignore` matching semantics: comments,
negation (`!`), anchored names (`/name`), `*`, `**`, `?`,
directory suffixes and escaped characters. The Rust generator uses
`git check-ignore --no-index` in an isolated temporary Git repository
to evaluate exactly these rules, independently of the real repository's
`.gitignore`, developer global ignores or working-tree state. Rules are
read from **the same pinned Git commit** as the source files, not from
an uncommitted or subsequently edited `.rgpignore`. Absent
`.rgpignore`, the generator behaves as before.

Paths selected for exclusion are never read as source content. They are
reported in the sorted manifest `excluded` array with reason
`rgpignore`. The `.rgpignore` file itself is always included,
even if it contains `*`: the agent needs to understand the selection.

`snapshot_complete: true` means all files **within the selected source
scope** are available. Intentionally excluded files do not reduce
scoped completeness; `excluded_by_reason.rgpignore` and
`intentionally_ignored_files` still disclose their count.
It **does not mean** the entire Git repository is captured. Other reasons
(`sensitive_path`, `size_limit`, `non_text`, etc.) still make the
selected scope partial. Files excluded by .rgpignore can be requested
directly from GitHub at the exact source revision if needed.
Changing the committed ignore rules changes the source revision and
input digest, so stale snapshots remain detectable.

**Compatibility:** The Rust verifier and stdlib Python Reader
recognize RGP/1.0, 1.1, 1.2, 2.0 and 2.1. The old 1.1 guide remains
pinned for historical verification; the new 2.1 guide is independently
length/hash checked. Existing golden fixtures are not rewritten.

`manifest.json` is compact UTF-8 JSON of this shape:

```json
{
  "schema_version": "1.0",
  "source_commit": "<Git SHA>",
  "input_tree_digest": "<hex SHA-256>",
  "files": [
    {"path":"README.md", "mode":"100644", "kind":"file", "size":42, "sha256":"<hex>", "blob":"files/<hex>", "status":"included"}
  ],
  "excluded": [{"path":".env", "reason":"sensitive_path"}],
  "index_coverage":"not_indexed",
  "graph_coverage":"not_indexed"
}
```

The actual fields are emitted in this order by the Rust serializer. `files` and `excluded` are lexicographically sorted by original case-sensitive UTF-8 path. Input digest is SHA-256 of each included file's `path UTF-8 + NUL + mode ASCII + NUL + raw 32-byte SHA-256`, in sorted order. It intentionally excludes `source_commit`, `REPO_CONTEXT.rgp` and excluded file content. Thus a commit changing *only* the artifact does not make the snapshot stale; exclusions can affect completeness independently of freshness.

`mode=120000` has `kind=symlink` and stores the raw Git link-target bytes, not the dereferenced content. `status=pointer_only` denotes an unhydrated LFS pointer. Submodule gitlinks and files excluded due to conservative publication policy or size limits are reported in `excluded`. Paths must be relative, UTF-8, without backslash, control chars, `.`/`..`, empty segments or case-insensitive collisions. Paths outside this domain currently cause a clear generation failure. A single blob is limited to 16 MiB, total included content to 128 MiB; the current plaintext transport is limited to 192 MiB. This is a software safety guardrail, **not** a demonstrated connector size limit.

Schema and format are versioned separately from the Rust crate version. Unknown major versions are rejected; unknown optional future fields from compatible minor versions may be ignored. This proof-of-concept accepts manifest schema `1.0` and transports `RGP/1.0`, `RGP/1.1`, `RGP/1.2` or `RGP/2.0` or `RGP/2.1`. The embedded guide provides discoverability, not instruction authority. The agent still needs an independently trusted Reader or a carefully bounded Python stdlib parser. No executable Reader is embedded or automatically imported.

## Compatibility test vectors and planned SQL index

See [`tests/fixtures/v1/`](../tests/fixtures/v1/) for deterministic positive and negative transport vectors with exact SHA-256 expectations and [`docs/INDEX_SCHEMA.md`](INDEX_SCHEMA.md) for the future SQLite index schema (`schema.sql`). The current MVP-A archive **does not** include a database; a SQL contract is not a claim of completed semantic indexing. CI checks these fixtures against Rust verification and the independent stdlib Python Reader.

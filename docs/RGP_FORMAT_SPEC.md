# RGP transport and snapshot specification (PoC)

RGP/2.0 is a **plain UTF-8 file**, with original source texts directly
visible to a human or text-reading coding agent. It contains NO ZIP, Base64,
compression, escaped source content, or executable instructions:

```text
RGP/2.0
manifest_length=<decimal UTF-8 bytes>
manifest_sha256=<SHA-256 of exactly those JSON bytes>
--
<compact manifest JSON occupying exactly manifest_length UTF-8 bytes>
FILE	"src/main.rs"	<source byte length>	<source SHA-256>
<exact original UTF-8 source bytes>
FILE	"README.md"	<source byte length>	<source SHA-256>
<exact original UTF-8 source bytes>
END RGP
```

After the JSON manifest and each source, there is **one transport LF
delimiter**, not part of the content. Source sections are sorted by Git path.
Source content is delimited by its exact **UTF-8 byte count**, not by lines,
Markdown fences, sentinel strings or a guessed terminator. Embedded `END RGP`,
tab, CRLF and blank lines in source cannot terminate sections. The file
must be valid UTF-8 and contain no NUL characters.

The unchanged manifest schema `1.0` lists included files, their original
Git SHA-256, modes, paths and explicitly excluded files. Non-UTF8 and
NUL-bearing binary files are excluded with reason `non_text`; these cannot
be represented as readable text without encoding. A partial snapshot is
reported honestly, not called complete. The safe source rules and size
caps still apply. Symlink targets are represented as text only, never
followed or executed.

The generator emits a sibling `.rgpmi` (RGP-MI/1.0 JSON): filename,
`artifact_sha256` of the **whole RGP plaintext file**, byte length,
source commit, snapshot completeness, transport `RGP/2.0`, and the
[AI agent instructions](RGPMI_AGENT_GUIDE.txt). Both files should be
obtained from a trusted pinned Git publication revision. A text-only
GitHub connector can now read selective line windows of `.rgp` without
decoding anything; requesting the **entire** .rgp still consumes context.

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

**Compatibility:** The Rust verifier and Python stdlib Reader still
read historical RGP/1.0, RGP/1.1 (with its exact pinned
[legacy guide](RGP_AGENT_GUIDE.txt)) and RGP/1.2 (Base64 ZIP). The new
format is a major version to reflect the incompatible transport change.
Frozen legacy test vectors are unchanged. RGP/2.0 must be read by an
updated Reader. RGP-MI/1.0 accepts both the last ZIP transport 1.2 and
the new plaintext transport 2.0.

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

`mode=120000` has `kind=symlink` and stores the raw Git link-target bytes, not the dereferenced content. `status=pointer_only` denotes an unhydrated LFS pointer. Submodule gitlinks and files excluded due to conservative publication policy or size limits are reported in `excluded`. Paths must be relative, UTF-8, without backslash, control chars, `.`/`..`, empty segments or case-insensitive collisions. Paths outside this domain currently cause a clear generation failure. A single blob is limited to 16 MiB, total included content to 128 MiB; the transport ZIP is limited to 64 MiB. This is a software safety guardrail, **not** a demonstrated connector size limit.

Schema and format are versioned separately from the Rust crate version. Unknown major versions are rejected; unknown optional future fields from compatible minor versions may be ignored. This proof-of-concept accepts manifest schema `1.0` and transports `RGP/1.0`, `RGP/1.1`, `RGP/1.2` or `RGP/2.0`. The sidecar provides discovery and guidance, not an instruction authority. The agent still needs an independently trusted Reader or a carefully bounded Python stdlib parser. No executable Reader is embedded or automatically imported.

## Compatibility test vectors and planned SQL index

See [`tests/fixtures/v1/`](../tests/fixtures/v1/) for deterministic positive and negative transport vectors with exact SHA-256 expectations and [`docs/INDEX_SCHEMA.md`](INDEX_SCHEMA.md) for the future SQLite index schema (`schema.sql`). The current MVP-A archive **does not** include a database; a SQL contract is not a claim of completed semantic indexing. CI checks these fixtures against Rust verification and the independent stdlib Python Reader.

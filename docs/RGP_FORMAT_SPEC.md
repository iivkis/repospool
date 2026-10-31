# RGP transport and snapshot specification (PoC)

A RGP file is ASCII text with **LF-only** line endings. The generator
emits **RGP/1.2** with *no agent instructions in the transport*:

```text
RGP/1.2
payload_length=<decimal byte length of ZIP>
payload_sha256=<64 lowercase hex SHA-256 of ZIP>
base64_line_width=76
--
<canonical padded base64 ZIP in 76-character lines except the final line>
END RGP
```

Next to `example.rgp` the generator writes `example.rgpmi`, a small
deterministic UTF-8 JSON file (`format: RGP-MI/1.0`) containing:
`artifact` (sibling basename), `transport`, `artifact_sha256` (SHA-256 of
the **complete RGP text bytes**), `artifact_bytes`, `source_commit`,
`snapshot_complete` and `instructions`. The instructions come from
[docs/RGPMI_AGENT_GUIDE.txt](RGPMI_AGENT_GUIDE.txt). A sidecar is read first,
then the RGP can be downloaded as a **file** from an ordinary Git HTTP
endpoint or any other file delivery method. GitHub Actions is not required.
Neither the sidecar nor a raw URL proves authenticity; pin the Git revision
that published both files. The stdlib-only `python/rgpmi.py` checks the
sidecar, complete artifact hash, manifest commit, coverage and Reader
validation.

**Compatibility:** RGP/1.0 retains its exact historical four-line header;
RGP/1.1 retains its exact 1331-byte embedded canonical guide solely for
reading older files. The unchanged
[legacy guide](RGP_AGENT_GUIDE.txt) is checked byte-for-byte by Rust and by
the SHA-256 pinned in the Python Reader. RGP/1.2 has no inline guide and
does not require a sidecar to be decoded by the Reader, though the sidecar
is needed for the standard agent bootstrap and external authenticity check.
Old Readers do not support 1.2 without upgrading. The immutable 1.0 golden
fixtures remain untouched.

`payload_length` counts **decoded ZIP bytes**, not the outer text; hash is on the ZIP bytes. The Base64 portion is nonempty, uses RFC 4648 standard alphabet with padding, and has one LF after each line. An exact terminal `END RGP\n` marker is required. Reader decodes strictly, checks ZIP length and SHA **before** opening the ZIP. Any transport that can omit, reorder or duplicate bytes is unsupported until tested; a raw textual representation alone cannot guarantee correct delivery.

ZIP/DEFLATE entries are ordered: `manifest.json`, then distinct content blobs `files/<sha256>` in lexicographic order. ZIP timestamps are normalized to 1980-01-01, file permissions are normalized to 0644 and entries have no directory traversal. Compression and ZIP headers must be deterministic for a given generator and dependency version. Blob content is stored **exactly**, including binary bytes, encoding and line endings. Equal content is deduplicated.

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

Schema and format are versioned separately from the Rust crate version. Unknown major versions are rejected; unknown optional future fields from compatible minor versions may be ignored. This proof-of-concept accepts manifest schema `1.0` and transport `RGP/1.0`, `RGP/1.1` or `RGP/1.2`. The sidecar is discoverability metadata and guidance, not an authority or code loader. The agent still needs an independently trusted Reader or a carefully bounded Python stdlib parser. No executable Reader is embedded or automatically imported.

## Compatibility test vectors and planned SQL index

See [`tests/fixtures/v1/`](../tests/fixtures/v1/) for deterministic positive and negative transport vectors with exact SHA-256 expectations and [`docs/INDEX_SCHEMA.md`](INDEX_SCHEMA.md) for the future SQLite index schema (`schema.sql`). The current MVP-A archive **does not** include a database; a SQL contract is not a claim of completed semantic indexing. CI checks these fixtures against Rust verification and the independent stdlib Python Reader.

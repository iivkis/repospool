# RGP 1.0 transport and snapshot specification (PoC)

A RGP file is UTF-8-compatible ASCII. Line endings are **LF only**. The v1 header is exact, in this order:

```text
RGP/1.0
payload_length=<decimal byte length of ZIP>
payload_sha256=<64 lowercase hex SHA-256 of ZIP>
base64_line_width=76
--
<canonical padded base64 ZIP in lines of 76 characters except the final line>
END RGP
```

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

Schema and format are versioned separately from the Rust crate version. Unknown major versions are rejected; unknown optional future fields from compatible minor versions may be ignored. This proof-of-concept accepts schema `1.0` and transport header `RGP/1.0` exactly; support for compatible minor schemas is reserved for a later revision. No executable Reader is embedded or automatically imported.

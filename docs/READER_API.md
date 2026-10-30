# Trusted Reader API: MVP-A resource and coverage contract

`python/rgp_reader.py` is Python stdlib-only and treats the RGP archive as untrusted **data**. It never imports code from the ZIP. Constructing `Reader(path_or_bytes)` verifies the transport checksum, ZIP limits and paths, every blob hash, `manifest.json`, and the input tree digest before it answers requests.

## API

```python
from rgp_reader import Reader, RgpError
reader = Reader("REPO_CONTEXT.rgp")
reader.overview()
reader.validate()
reader.coverage("snapshot")  # or index, graph, investigation
page = reader.tree(prefix="src/", limit=100)
while page["next_cursor"] is not None:
    page = reader.tree(prefix="src/", limit=100, cursor=page["next_cursor"])
reader.read("README.md", start_line=1, end_line=40)
exact_git_blob = reader.read_bytes("README.md")  # verified raw bytes, no decoding
page = reader.search("TODO", paths=["README.md"], limit=10)
while page["next_cursor"] is not None:
    page = reader.search("TODO", paths=["README.md"], limit=10, cursor=page["next_cursor"])
# Search a whole directory without enumerating all files first:
page = reader.search("TODO", prefix="src/", limit=10)
while page["next_cursor"] is not None:
    page = reader.search("TODO", prefix="src/", limit=10, cursor=page["next_cursor"])
```

`read_bytes(path)` is a **public Python-only verified raw source API**.
It preserves exact Git blob bytes including binary data and line endings,
without interpreting symlinks or importing source. Unknown/omitted paths
raise `PARTIAL_SNAPSHOT`. It is not a 256 KiB JSON response; individual
blobs remain bounded by verified Reader resource limits. It supports
[safe sandbox source workspaces](SANDBOX_SOURCE_WORKSPACE.md), which
never depend on the Reader's private ZIP implementation.

A search result contains its exact `path` and 1-based `line`, plus a
**match-aware excerpt** bounded to 512 UTF-8 bytes. For queries that fit in
512 bytes, the excerpt includes the first occurrence of the queried string
even if that occurrence lies thousands of characters into a long source or
minified line. A query longer than 512 bytes cannot fit entirely; only the
beginning of its matching text is shown. To inspect full surrounding source,
use `reader.read(path, start_line=line, end_line=line)`, subject to the
independent 256 KiB read-response bound. Excerpts are display snippets,
not complete source lines or trustworthy provenance evidence.

CLI equivalents: `python3 python/rgp_reader.py --artifact REPO_CONTEXT.rgp coverage --scope snapshot`, `tree --prefix src/ --limit 20 --cursor TOKEN`, `search TODO --path README.md --limit 20 --cursor TOKEN`, or `search TODO --prefix src/ --limit 20 --cursor TOKEN`.

`search(prefix="src/")` matches a **lexical path prefix**. Include the trailing slash for a directory: `prefix="src"` also matches paths under `src2/`. An empty prefix preserves whole-snapshot search. When exact `paths` and `prefix` are both supplied, all explicitly requested paths must belong to that prefix; an inconsistent combination raises `UNSUPPORTED_FORMAT`. Missing exact paths still raise `PARTIAL_SNAPSHOT`. Empty results carry the snapshot's existing completeness status, not a promise that excluded files were searched. Unsafe or oversized prefixes are rejected.

A `next_cursor` is a *string*, not a numeric offset. Pass it back unchanged with the **same** query, path scope, prefix, and snapshot. It is bound to the `input_tree_digest` and query parameters; a mismatched or malformed cursor raises `UNSUPPORTED_FORMAT`. Cursors are stable for the same immutable snapshot, even after a different commit with identical snapshot contents. They do **not** authenticate the archive's origin.

Malformed Unicode from a model's search/tool arguments must be a
**recoverable, typed error**, not an uncaught Python exception. In particular,
unpaired UTF-16 surrogate code units do not represent valid UTF-8 Git
paths or search terms. The Reader returns `UNSAFE_PATH` for such
manifest paths, `UNSUPPORTED_FORMAT` for invalid `tree` prefixes
and `search` terms, and the context CLI reports invalid term arguments.
MCP keeps answering subsequent tool requests, including after a
surrogate-containing JSON-RPC request identifier.

## Resource limits (v1.0 Reader)

| Boundary | Maximum |
|---|---:|
| Outer text | 128 MiB |
| ZIP payload | 64 MiB |
| Total ZIP entries | 50,000 |
| Total uncompressed ZIP data | 128 MiB |
| A single content blob | 16 MiB |
| `manifest.json` | 16 MiB |
| ZIP expansion ratio | 200×, with a 4 KiB allowance per member |
| UTF-8 source/ZIP path | 4,096 bytes |
| A single `read()` response content | 256 KiB, **including Base64 expansion** |
| Tree result | 200 paths |
| Search result | 100 hits; 512 UTF-8 bytes per excerpt |
| Search query | 1,024 UTF-8 bytes |
| Cursor token | 8,192 characters |
| Exclusion rows returned in overview | 100, with `excluded_truncated` indicator |

Limits are validation policies, not guarantees of fixed peak process RSS or wall time. Construction currently holds decoded ZIP and verified content blobs in RAM, so peak memory exceeds the uncompressed-byte ceiling. ZIP is never extracted with `extractall()`. Queries over large files may scan them; the v1 Reader does not promise indexed or constant-time search.

## Explicit completeness

`overview()` and `coverage("snapshot")` report `included_files`, `pointer_only_files`, `excluded_files`, `excluded_by_reason`, and `snapshot_complete`. An intentionally excluded `REPO_CONTEXT.rgp` (`self_artifact`) is not treated as a missing source file. Sensitive, oversized or unavailable submodule paths, and unhydrated LFS pointers, mark the snapshot `partial`.

`tree()` and `search()` return `snapshot_status` so an empty result in a partial snapshot cannot be confused with proof of absence. An explicitly requested path missing from the snapshot causes `PARTIAL_SNAPSHOT` instead of silently searching nothing. `coverage("index")` and `coverage("graph")` return `not_indexed`; `coverage("investigation")` returns `unknown`, because tool disclosure does not prove what an agent understood.

This contract is a foundation for **Gate B**, not evidence that RGP reduces agent tokens, calls, or time. Gate B requires controlled comparisons against the standard connector workflow.

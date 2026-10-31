# Trusted Reader API: MVP-A resource and coverage contract

`python/rgp_reader.py` использует только стандартную библиотеку
Python и считает любой RGP недоверенными данными. Для текущего
**RGP/3.0** создание `Reader` проверяет UTF-8, границы байтов,
порядок путей, дубликаты, NUL и ресурсные лимиты; SHA/Git commit
**отсутствуют в транспорте**. Метаданные SHA в API вычисляются
локально и **не подтверждают подлинность** исходников. Проверка
доверенного источника выполняется **вне Reader**, по Git blob
или независимому SHA-256, связанному с исходной Git-ревизией.

Для старых RGP/1.0–1.2 Reader сохраняет проверку ZIP/Base64
и внутренних SHA, а для RGP/2.0–2.1 проверяет встроенные
хеши UTF-8-транспорта. Не путать исторические свойства с 3.0.

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
exact_git_blob = reader.read_bytes("README.md")  # exact RGP bytes, NOT independently Git-verified
page = reader.search("TODO", paths=["README.md"], limit=10)
while page["next_cursor"] is not None:
    page = reader.search("TODO", paths=["README.md"], limit=10, cursor=page["next_cursor"])
# Search a whole directory without enumerating all files first:
page = reader.search("TODO", prefix="src/", limit=10)
while page["next_cursor"] is not None:
    page = reader.search("TODO", prefix="src/", limit=10, cursor=page["next_cursor"])
```

`read_bytes(path)` is a **public Python-only raw source API**. It verifies the internal structure and length; on RGP/3.0 it does not prove those bytes equal a Git blob.
It preserves exact Git blob bytes including binary data and line endings,
without interpreting symlinks or importing source. Unknown/omitted paths
raise `PARTIAL_SNAPSHOT`. It is not a 256 KiB JSON response; individual
blobs remain bounded by Reader resource limits. It supports
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

## Resource limits

| Ограничение | RGP/3.0 | Исторические RGP/1.x/2.x |
| --- | --- | --- |
| Полный текст RGP | 192 MiB | Отдельные транспортные лимиты |
| Отдельный файл | 16 MiB | 16 MiB |
| Индекс / JSON-манифест | 16 MiB (text index) | 16 MiB (manifest) |
| Длина пути | 4096 байт UTF-8 | 4096 байт UTF-8 |
| Число файлов | 50 000 (верхняя граница) | Лимиты по форматам |
| ZIP-разжатие | **Отсутствует** | Проверка количества, размера и 200× ratio |
| Содержимое одного `read()` | 256 KiB | 256 KiB |
| `tree()` | до 200 путей | до 200 |
| `search()` | до 100 результатов, 512 байт на snippet | то же |
| Строка запроса `search()` | 1024 UTF-8 байта | то же |
| Cursor | 8192 символов | то же |
| Обзор исключений | до 100 записей | то же |

Общий объём и память ограничены; Reader в текущем виде может
держать полное содержимое в памяти и сканировать строки при поиске.
Лимит файла не обещает фиксированной задержки или RSS.
Текстовый RGP/3.0 не является сжатым ZIP-архивом.

## Explicit completeness

`overview()` and `coverage("snapshot")` report `included_files`, `pointer_only_files`, `excluded_files`, `excluded_by_reason`, and `snapshot_complete`. An intentionally excluded `REPO_CONTEXT.rgp` (`self_artifact`) is not treated as a missing source file. The configured `.rgpignore` removes deliberately omitted paths from scope, while sensitive, oversized, unavailable submodule paths and unhydrated LFS pointers mark the selected snapshot `partial`. For RGP/3.0 ignored paths are reported by count, not as a full enumerated list.

`tree()` and `search()` return `snapshot_status` so an empty result in a partial snapshot cannot be confused with proof of absence. An explicitly requested path missing from the snapshot causes `PARTIAL_SNAPSHOT` instead of silently searching nothing. `coverage("index")` and `coverage("graph")` return `not_indexed`; `coverage("investigation")` returns `unknown`, because tool disclosure does not prove what an agent understood.

This contract is a foundation for **Gate B**, not evidence that RGP reduces agent tokens, calls, or time. Gate B requires controlled comparisons against the standard connector workflow.

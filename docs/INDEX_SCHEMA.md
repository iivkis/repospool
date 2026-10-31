# Optional semantic index design (not implemented)

`schema.sql` is a **future research proposal**, not an emitted RGP component or supported Reader API. The sole supported transport is `RGP/0.1.0`: an automatically generated universal guide, a compact index of paths and sizes, and length-framed UTF-8 source sections. It contains no JSON manifest, ZIP payload, hashes, SQLite database or semantic graph.

## Proposed contract

- `snapshot` would bind local analysis to independently authenticated Git source bytes and a derived in-memory digest.
- `files` would record paths, modes and source fingerprints; it would not replace original UTF-8 content.
- `analysis_runs` would identify producers and configurations of parser/analyzer runs.
- `symbols` would store source positions, not unverified runtime behavior.
- `relations` would distinguish observed syntactic links, resolved references and uncertain dependencies.
- `coverage` would distinguish available snapshot data from partial, unsupported or not-indexed semantic analysis.

The schema's referential constraints are exercised in `tests/test_index_schema.py`, but passing these tests **does not mean that RGP produces or consumes a semantic index**. There is currently no SQL-backed Reader.

## Future security boundary

If a semantic index is ever implemented, it must be generated from source tied to a separately trusted Git revision and authenticated RGP file. Treat derived index data as untrusted unless its production and provenance are established. Open any future SQLite database in read-only/query-only mode with limits; do not execute embedded scripts or SQL from repository content.

Past ZIP/Base64 fixtures and compatibility tasks have been removed from the active codebase. Old experimental materials may be consulted in Git history, but they do not describe the supported `RGP/0.1.0` format.

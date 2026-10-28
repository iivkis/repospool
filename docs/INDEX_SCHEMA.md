# SQLite index design v0.1 (not yet produced)

`schema.sql` records the **future** local index contract. As of MVP-A, Rust generates `manifest.json` and `files/<sha256>` only, and Python Reader operates directly on those verified blobs. No symbols, relations, or SQL lookup are currently exposed. Shipping an empty database as if it were completed semantic analysis would misrepresent coverage.

## Contract

- `snapshot` binds the index to `input_tree_digest`. `source_commit` is provenance, not the freshness rule.
- `files` references original stored content by digest and path; it never normalizes or substitutes source bytes.
- `analysis_runs` records the producer, version, configuration and status. A Rust feature set or changed analyzer configuration requires a distinct run.
- `symbols` stores *positions* and confidence, never a claim of runtime behavior.
- `relations` distinguishes `imports`, `references`, `calls`, `implements`, `contains`, `tests`, and `configures`. Each relation has evidence location, producer (`run_id`) and one resolved or unresolved target. `confirmed_reference` requires an actually resolved symbol. Merely importing something is not a confirmed runtime call.
- `coverage` stores `snapshot`, `index`, `graph` and `investigation` separately. Investigation coverage can describe what the Reader **returned**, not what an agent internally understood.

SQLite foreign keys and constraints are tested in Python stdlib (`tests/test_index_schema.py`). The DDL intentionally avoids optional FTS5/JSON1 and `STRICT` features, and uses `PRAGMA user_version=1` to allow future migrations. **Compatibility with a final minimum SQLite release is not claimed** until the reader sandbox has been tested, including its bundled SQLite version.

## Trust boundary for the planned reader

The index is untrusted ZIP content. A future reader must first validate transport length/SHA, ZIP paths, hashes, expanded sizes and schema; only then extract a verified SQLite file to a private temporary directory. Open it with a read-only URI and `PRAGMA query_only=ON`. Never execute SQL, scripts, extensions, triggers or embedded Python received from the container. The SQLite schema is a query model, not an endorsement of archive authenticity.

## Golden fixtures

`tests/fixtures/v1/` contains seven small synthetic, version-pinned archives with an independent `expected.json` containing exact byte counts and SHA-256. The collection covers valid mixed/empty snapshots and truncation, external payload-hash corruption, ZIP-slip, manifest digest mismatch and unsupported transport versions.

The mixed snapshot includes CRLF/LF, non-UTF8 binary data, a Unicode path, a symlink **as link bytes**, an LFS pointer marked `pointer_only`, a deduplicated blob and explicit exclusions. The test harness validates the trusted Python Reader. CI additionally validates the same files through the Rust verifier; neither tool is allowed to silently reinterpret invalid archives as valid.

```sh
task contract:golden       # verify committed fixtures without Cargo
task contract:cross        # build Rust CLI + check both validators
# Intentionally regenerate committed golden bytes only when changing the format:
python3 scripts/golden_fixtures.py write
```

These fixtures test format compatibility and byte integrity, **not** a real GitHub connector, source authenticity, an analyzer, or Gate B. The checks reject mutations of committed golden bytes unless the expected hashes and tested format are deliberately updated.

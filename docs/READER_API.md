# Python Reader API (RGP/0.1.0)

The stdlib-only `python/rgp_reader.py` accepts **only** RGP/0.1.0. It is a trusted local parser for an otherwise untrusted source snapshot. It does not import, execute, or dereference archived files.

```sh
python3 python/rgp_reader.py --artifact source.rgp validate
python3 python/rgp_reader.py --artifact source.rgp overview
python3 python/rgp_reader.py --artifact source.rgp tree --prefix src/
python3 python/rgp_reader.py --artifact source.rgp read --path src/main.rs --start-line 10 --end-line 25
python3 python/rgp_reader.py --artifact source.rgp search "function" --prefix src/
python3 python/rgp_reader.py --artifact source.rgp coverage --scope snapshot
```

Python interface: `Reader(path_or_bytes).validate()`, `.overview()`, `.tree(prefix, limit, cursor)`, `.read(path, start_line, end_line)`, `.read_bytes(path)`, `.search(query, paths, limit, cursor, prefix=...)`, and `.coverage(scope)`. Results are bounded; tree and search cursors are tied to the query and snapshot.

`overview().source_commit` is always `None`: this information is not present in RGP/0.1.0. The reader derives temporary content fingerprints in memory for API compatibility; they are not provenance. `overview().embedded_instructions` contains the generic, auto-generated transport guide. `index_coverage` and `graph_coverage` return `not_indexed`.

`validate()` checks syntax, UTF-8, paths, sizes, section framing, ordering and resource limits. It **does not authenticate** the file. Obtain a trusted complete-artifact digest and intended Git revision independently, then compare significant source bytes with Git when needed. `.rgpignore` exclusions limit snapshot scope and are not evidence of absent repository files.

Old transports are rejected with `UNSUPPORTED_FORMAT`. The Reader is a read-only API and cannot write commits or substitute for a Git checkout.

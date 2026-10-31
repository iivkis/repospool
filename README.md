# RGP

**RGP/0.1.0** packs selected Git-tracked UTF-8 files for coding agents. Each read-only snapshot has an auto-generated, repository-neutral guide, file index, and byte-framed source.

```sh
cargo run --locked -- generate --repo . --output source.rgp
cargo run --locked -- verify --artifact source.rgp --repo .
python3 python/rgp_reader.py --artifact source.rgp tree
python3 python/rgp_reader.py --artifact source.rgp read --path README.md
```

RGP embeds **no commit IDs or hashes**: verify provenance against trusted Git data. `.rgpignore` limits snapshot scope. Old format versions are unsupported.

[Format](docs/RGP_FORMAT_SPEC.md) · [Reader](docs/READER_API.md) · [Security](docs/RGP_SECURITY_MODEL.md)

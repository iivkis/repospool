default:
    @just --list

fmt:
    cargo fmt --all

check:
    cargo fmt --all -- --check
    cargo clippy --locked --all-targets -- -D warnings
    cargo test --locked
    uv run --no-project python -m unittest discover -s tests -p 'test_*.py' -v

smoke:
    cargo run --locked -- generate --repo . --output /tmp/rgp-smoke.rgp
    cargo run --locked -- verify --artifact /tmp/rgp-smoke.rgp --repo .
    uv run --no-project python python/rgp_reader.py --artifact /tmp/rgp-smoke.rgp overview

"""Maintain deterministic v1 contract fixtures (stdlib-only, independent of Rust).

`write` intentionally changes version-controlled golden bytes. `check` regenerates
fixtures and requires exact equality; the Rust CLI may be supplied for parity.
These are *synthetic* snapshots, not proof of completeness of a Git tree.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import subprocess
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "fixtures" / "v1"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fixture_files() -> list[tuple[str, str, bytes, str]]:
    lfs_pointer = (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + b"1" * 64 + b"\nsize 100000000\n"
    )
    readme = b"# sample\r\nUTF-8 and bytes are preserved.\n"
    return [
        ("README.md", "100644", readme, "included"),
        ("assets/raw.bin", "100644", bytes(range(256)) + b"\x00\xff", "included"),
        ("lfs/large.bin", "100644", lfs_pointer, "pointer_only"),
        ("scripts/link", "120000", b"../README.md", "included"),
        ("src/copy.md", "100644", readme, "included"),
        ("src/пример.py", "100644", "def ответ():\n    return 42\n".encode(), "included"),
    ]


def manifest_and_blobs(mixed: bool = True) -> tuple[dict, dict[str, bytes]]:
    files = []
    blobs: dict[str, bytes] = {}
    tree = hashlib.sha256()
    for path, mode, content, status in (fixture_files() if mixed else []):
        digest = sha(content)
        entry = {
            "path": path, "mode": mode,
            "kind": "symlink" if mode == "120000" else "file",
            "size": len(content), "sha256": digest,
            "blob": f"files/{digest}", "status": status,
        }
        files.append(entry)
        blobs[entry["blob"]] = content
        tree.update(path.encode() + b"\0" + mode.encode("ascii") + b"\0" + bytes.fromhex(digest))
    manifest = {
        "schema_version": "1.0", "source_commit": "c" * 40,
        "input_tree_digest": tree.hexdigest(),
        "files": files,
        "excluded": ([
            {"path": ".env", "reason": "sensitive_path"},
            {"path": "REPO_CONTEXT.rgp", "reason": "self_artifact"},
            {"path": "vendor/submodule", "reason": "submodule_unavailable"},
        ] if mixed else []),
        "index_coverage": "not_indexed", "graph_coverage": "not_indexed",
    }
    return manifest, blobs


def zip_payload(manifest: dict, blobs: dict[str, bytes], extra: tuple[str, bytes] | None = None) -> bytes:
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipout:
        for name, content in [("manifest.json", json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode()),
                              *sorted(blobs.items()), *([extra] if extra else [])]:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Duplicate name")
                zipout.writestr(info, content)
    return memory.getvalue()


def transport(payload: bytes) -> bytes:
    encoded = base64.b64encode(payload)
    header = (f"RGP/1.0\npayload_length={len(payload)}\n"
              f"payload_sha256={sha(payload)}\nbase64_line_width=76\n--\n").encode()
    return header + b"".join(encoded[i:i + 76] + b"\n" for i in range(0, len(encoded), 76)) + b"END RGP\n"


def generate() -> tuple[dict[str, bytes], dict]:
    mixed, blobs = manifest_and_blobs()
    empty, empty_blobs = manifest_and_blobs(False)
    valid = transport(zip_payload(mixed, blobs))
    empty_valid = transport(zip_payload(empty, empty_blobs))
    bad_tree = {**mixed, "input_tree_digest": "0" * 64}
    # Flip the first hash digit without changing field width.
    header, tail = valid.split(b"\n--\n", 1)
    original_hash = sha(zip_payload(mixed, blobs))
    header = header.replace(original_hash.encode(), ("0" if original_hash[0] != "0" else "1").encode() + original_hash[1:].encode())
    corrupt_sha = header + b"\n--\n" + tail
    artifacts = {
        "valid-mixed.rgp": valid,
        "valid-empty.rgp": empty_valid,
        "invalid-truncated.rgp": valid[:-12],
        "invalid-payload-sha.rgp": corrupt_sha,
        "invalid-zip-slip.rgp": transport(zip_payload(mixed, blobs, ("../escape.txt", b"nope"))),
        "invalid-tree-digest.rgp": transport(zip_payload(bad_tree, blobs)),
        "invalid-unsupported-version.rgp": valid.replace(b"RGP/1.0\n", b"RGP/2.0\n", 1),
    }
    expected = {
        "format": "RGP/1.0",
        "valid": ["valid-mixed.rgp", "valid-empty.rgp"],
        "invalid": {
            "invalid-truncated.rgp": "TRUNCATED_ARTIFACT",
            "invalid-payload-sha.rgp": "HASH_MISMATCH",
            "invalid-zip-slip.rgp": "UNSAFE_PATH",
            "invalid-tree-digest.rgp": "HASH_MISMATCH",
            "invalid-unsupported-version.rgp": "UNSUPPORTED_FORMAT",
        },
        "mixed_input_tree_digest": mixed["input_tree_digest"],
        "mixed_files": mixed["files"],
        "mixed_excluded": mixed["excluded"],
        "artifacts": {name: {"bytes": len(data), "sha256": sha(data)} for name, data in sorted(artifacts.items())},
    }
    return artifacts, expected


def run_check(rust_cli: Path | None) -> None:
    artifacts, expected = generate()
    expected_bytes = (json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    assert (TARGET / "expected.json").read_bytes() == expected_bytes, "golden expected.json drift"
    for name, data in artifacts.items():
        assert (TARGET / name).read_bytes() == data, f"golden fixture drift: {name}"
        if rust_cli is not None:
            result = subprocess.run([str(rust_cli.resolve()), "verify", "--artifact", str(TARGET / name)],
                                    capture_output=True, text=True, check=False)
            should_pass = name in expected["valid"]
            assert (result.returncode == 0) == should_pass, f"Rust verifier parity: {name}: {result.stderr}"
            if not should_pass:
                assert expected["invalid"][name] in result.stderr, f"wrong Rust error class: {name}: {result.stderr}"
    print(f"Verified {len(artifacts)} immutable golden RGP fixtures" + (" against Rust" if rust_cli else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("write", "check"))
    parser.add_argument("--rust-cli", type=Path, default=None)
    args = parser.parse_args()
    if args.command == "write":
        TARGET.mkdir(parents=True, exist_ok=True)
        artifacts, expected = generate()
        for name, data in artifacts.items():
            (TARGET / name).write_bytes(data)
        (TARGET / "expected.json").write_text(
            json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(artifacts)} golden RGP fixtures")
    else:
        run_check(args.rust_cli)


if __name__ == "__main__":
    main()

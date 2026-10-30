"""Deterministic, *valid* RGP transport fixtures and offline verification.

All fixtures contain synthetic pseudorandom Git-like blobs and a valid manifest.
This is a developer-only Python stdlib probe; the product generator is Rust.
The GitHub connector must be tested externally: local verification is NOT Gate A.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from rgp_reader import Reader  # noqa: E402

SIZES = (100 * 1024, 5 * 1024**2, 20 * 1024**2, 50 * 1024**2)
CHUNK_SIZE = 8 * 1024**2
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_fixture(target_size: int) -> tuple[bytes, dict]:
    """Make a valid RGP, within 1% of target text size (not repo size)."""
    if not 4096 <= target_size <= 50 * 1024**2:
        raise ValueError("target_size out of experimental range")
    # Base64 with 76 columns + LF costs ~77/57 bytes per ZIP byte.
    remaining = max(1024, (target_size - 800) * 57 // 77)
    files = []
    contents = []
    counter = 0
    while remaining:
        count = min(remaining, CHUNK_SIZE)
        data = hashlib.shake_256(f"rgp-transport-v1-{target_size}-{counter}".encode()).digest(count)
        path = f"probe/chunk-{counter:04d}.bin"
        digest = sha(data)
        files.append({
            "path": path, "mode": "100644", "kind": "file", "size": len(data),
            "sha256": digest, "blob": f"files/{digest}", "status": "included",
        })
        contents.append((f"files/{digest}", data))
        remaining -= count
        counter += 1
    tree = hashlib.sha256()
    for entry in files:
        tree.update(entry["path"].encode("utf-8") + b"\x00")
        tree.update(entry["mode"].encode("ascii") + b"\x00")
        tree.update(bytes.fromhex(entry["sha256"]))
    manifest = {
        "schema_version": "1.0", "source_commit": "0" * 40,
        "input_tree_digest": tree.hexdigest(), "files": files, "excluded": [],
        "index_coverage": "not_indexed", "graph_coverage": "not_indexed",
    }
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        def add(name: str, content: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 << 16)
            archive.writestr(info, content)

        add("manifest.json", json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode())
        for name, content in sorted(contents):
            add(name, content)
    payload = zipped.getvalue()
    encoded = base64.b64encode(payload)
    lines = b"".join(encoded[i:i + 76] + b"\n" for i in range(0, len(encoded), 76))
    header = (
        f"RGP/1.0\npayload_length={len(payload)}\n"
        f"payload_sha256={sha(payload)}\nbase64_line_width=76\n--\n"
    ).encode("ascii")
    result = header + lines + b"END RGP\n"
    if abs(len(result) - target_size) > target_size // 100:
        raise ValueError(f"text size deviates >1%: target {target_size}, got {len(result)}")
    expected = {
        "file": f"probe-{target_size}.rgp", "target_transport_bytes": target_size,
        "transport_bytes": len(result), "transport_sha256": sha(result),
        "payload_bytes": len(payload), "payload_sha256": sha(payload),
        "snapshot_bytes": sum(f["size"] for f in files),
        "input_tree_digest": manifest["input_tree_digest"], "file_count": len(files),
    }
    return result, expected


def verify_fixture(path: Path, expected: dict) -> None:
    """Verify entire received file, including inner RGP and every source blob."""
    received = path.read_bytes()
    if len(received) != expected["transport_bytes"] or sha(received) != expected["transport_sha256"]:
        raise ValueError(f"TRANSPORT_MISMATCH: {path.name}: length or SHA-256 changed")
    reader = Reader(received)
    overview = reader.overview()
    if (overview["input_tree_digest"] != expected["input_tree_digest"] or
            overview["file_count"] != expected["file_count"] or
            not overview["snapshot_complete"]):
        raise ValueError("MANIFEST_MISMATCH: snapshot metadata differs")
    for entry in reader.manifest["files"]:
        data = reader._blob(entry["path"])
        if sha(data) != entry["sha256"] or len(data) != entry["size"]:
            raise ValueError(f"HASH_MISMATCH: {entry['path']}")


def generate(directory: Path, sizes: list[int]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    metrics = []
    for size in sizes:
        result, expected = make_fixture(size)
        path = directory / expected["file"]
        path.write_bytes(result)
        verify_fixture(path, expected)
        metrics.append(expected)
        print(json.dumps(expected, sort_keys=True), flush=True)
    (directory / "expected.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")


def verify(directory: Path, expected_file: Path) -> None:
    records = json.loads(expected_file.read_text())
    if not isinstance(records, list) or not records:
        raise ValueError("invalid expected.json")
    for record in records:
        if Path(record["file"]).name != record["file"]:
            raise ValueError("unsafe fixture filename")
        verify_fixture(directory / record["file"], record)
        print(f"verified {record['file']}: {record['transport_bytes']} bytes", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate")
    create.add_argument("--output-dir", type=Path, required=True)
    create.add_argument("--sizes", nargs="+", type=int, default=SIZES)
    check = commands.add_parser("verify")
    check.add_argument("--input-dir", type=Path, required=True)
    check.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.output_dir, args.sizes)
    else:
        verify(args.input_dir, args.expected)


if __name__ == "__main__":
    main()

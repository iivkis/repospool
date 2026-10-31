"""Only legacy RGP/1.0 synthetic fixture generation for compatibility tests.

NOT a current RGP/3.0 transport, connector delivery probe, or benchmark.
Keep historical vectors isolated from current production examples.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

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


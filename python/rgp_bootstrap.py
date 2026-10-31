"""Trusted stdlib bootstrap for GitHub Actions ZIPs containing one RGP artifact.

This is an explicitly selected *transport adapter*, not the RGP file format.
No Python code or shell commands from the archive are ever executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from rgp_reader import MAX_ARTIFACT, MAX_RATIO, Reader, RgpError, _require

HEX = re.compile(r"[0-9a-f]{64}\Z")


def bootstrap(zip_path: Path, output: Path, trusted_sha256: str | None = None) -> dict:
    """Verify and atomically emit a trusted-Reader-compatible .rgp file.

    The co-packed `expected.json` is an integrity cross-check ONLY, not a
    trusted authenticity assertion. Pass a separately verified trusted SHA to
    authenticate the RGP bytes against an external trust anchor.
    """
    if trusted_sha256 is not None:
        _require(bool(HEX.fullmatch(trusted_sha256)), "UNSUPPORTED_FORMAT", "invalid trusted SHA-256")
    _require(zip_path.stat().st_size <= 2 * MAX_ARTIFACT, "RESOURCE_LIMIT", "ZIP wrapper too large")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            files = archive.infolist()
            _require(1 <= len(files) <= 2, "UNSUPPORTED_FORMAT", "expected one RGP and optional manifest")
            seen: set[str] = set()
            names = set()
            for info in files:
                name = info.filename
                _require(
                    bool(name) and "/" not in name and "\\" not in name and
                    name not in (".", "..") and not any(ord(c) < 32 or ord(c) == 127 for c in name),
                    "UNSAFE_PATH", "unsafe Actions ZIP entry",
                )
                _require(name.casefold() not in seen, "UNSAFE_PATH", "duplicate Actions ZIP entry")
                seen.add(name.casefold())
                # Reject symlinks; all supported members are ordinary file bytes.
                mode = (info.external_attr >> 16) & 0o170000
                _require(mode not in (0o120000, 0o040000), "UNSAFE_PATH", "non-file ZIP entry")
                _require(info.file_size <= 2 * MAX_ARTIFACT, "RESOURCE_LIMIT", "ZIP entry too large")
                _require(info.file_size <= max(4096, info.compress_size * MAX_RATIO), "RESOURCE_LIMIT", "ZIP compression ratio")
                names.add(name)
            rgps = [n for n in names if n.endswith(".rgp")]
            _require(
                len(rgps) == 1 and names - set(rgps) <= {"expected.json", "provenance.json"},
                "UNSUPPORTED_FORMAT", "unexpected Actions artifact files",
            )
            artifact_name = rgps[0]
            received = archive.read(artifact_name)
            _require(len(received) <= 2 * MAX_ARTIFACT, "RESOURCE_LIMIT", "RGP too large")
            digest = hashlib.sha256(received).hexdigest()
            provenance = None
            if "provenance.json" in names:
                meta_info = archive.getinfo("provenance.json")
                _require(meta_info.file_size <= 64 * 1024, "RESOURCE_LIMIT", "provenance.json too large")
                try:
                    provenance = json.loads(archive.read("provenance.json"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise RgpError("UNSUPPORTED_FORMAT", "invalid provenance.json") from exc
                _require(isinstance(provenance, dict), "UNSUPPORTED_FORMAT", "provenance.json shape")
                _require(provenance.get("rgp_sha256") == digest, "HASH_MISMATCH", "provenance RGP SHA-256")
                _require(type(provenance.get("rgp_bytes")) is int and
                         provenance["rgp_bytes"] == len(received), "HASH_MISMATCH", "provenance RGP bytes")
                _require(isinstance(provenance.get("plan_sha256"), str) and
                         bool(HEX.fullmatch(provenance["plan_sha256"])),
                         "UNSUPPORTED_FORMAT", "provenance benchmark plan SHA-256")
                _require(provenance.get("transport_verified") is False,
                         "UNSUPPORTED_FORMAT", "producer must not claim connector delivery")
            if "expected.json" in names:
                meta = archive.getinfo("expected.json")
                _require(meta.file_size <= 64 * 1024, "RESOURCE_LIMIT", "expected.json too large")
                try:
                    records = json.loads(archive.read("expected.json"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise RgpError("UNSUPPORTED_FORMAT", "invalid expected.json") from exc
                _require(isinstance(records, list) and len(records) == 1, "UNSUPPORTED_FORMAT", "expected.json shape")
                expected = records[0]
                _require(isinstance(expected, dict), "UNSUPPORTED_FORMAT", "expected.json row")
                _require(expected.get("file") == artifact_name, "HASH_MISMATCH", "expected.json filename")
                _require(expected.get("transport_bytes") == len(received), "HASH_MISMATCH", "expected.json RGP size")
                _require(expected.get("transport_sha256") == digest, "HASH_MISMATCH", "expected.json RGP hash")
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise RgpError("UNSUPPORTED_FORMAT", "invalid Actions ZIP wrapper") from exc
    _require(trusted_sha256 is None or trusted_sha256 == digest, "HASH_MISMATCH", "external trusted SHA-256")
    reader = Reader(received)
    if provenance is not None:
        _require(reader.manifest["source_commit"] in (None, provenance.get("source_commit")),
                 "HASH_MISMATCH", "provenance source commit")
        overview = provenance.get("reader_overview")
        _require(isinstance(overview, dict) and
                 overview.get("input_tree_digest") == reader.manifest["input_tree_digest"] and
                 overview.get("file_count") == len(reader.manifest["files"]),
                 "HASH_MISMATCH", "provenance snapshot metadata")
    result = reader.validate()
    output.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".rgp-", suffix=".tmp", delete=False) as temp:
            name = temp.name
            temp.write(received)
        os.replace(name, output)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)
    return {"artifact": str(output), "bytes": len(received), "sha256": digest, **result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actions-zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--trusted-sha256")
    args = parser.parse_args()
    try:
        result = bootstrap(args.actions_zip, args.output, args.trusted_sha256)
        print(json.dumps(result, indent=2))
    except (RgpError, OSError) as exc:
        print(f"BOOTSTRAP_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

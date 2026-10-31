"""Validate a small RGP-MI sidecar and its sibling artifact without running source code.

RGP-MI is a discoverability and integrity aid, not independent provenance.
Trust the Git revision containing BOTH files before using its metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from rgp_reader import Reader, RgpError, _require

MAX_METADATA = 16 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
FIELDS = {
    "format", "artifact", "transport", "artifact_sha256", "artifact_bytes",
    "source_commit", "snapshot_complete", "instructions",
}


def _unique_pairs(pairs):
    data = {}
    for key, value in pairs:
        _require(key not in data, "UNSUPPORTED_FORMAT", "duplicate metadata key")
        data[key] = value
    return data


def read_metadata(path: Path) -> dict:
    path = Path(path)
    _require(path.stat().st_size <= MAX_METADATA, "RESOURCE_LIMIT", "metadata too large")
    try:
        meta = json.loads(path.read_bytes().decode("utf-8"),
                          object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RgpError("UNSUPPORTED_FORMAT", "invalid metadata JSON") from exc
    _require(isinstance(meta, dict) and set(meta) == FIELDS,
             "UNSUPPORTED_FORMAT", "invalid metadata keys")
    _require(meta["format"] == "RGP-MI/1.0" and meta["transport"] == "RGP/1.2",
             "UNSUPPORTED_FORMAT", "metadata version")
    name = meta["artifact"]
    _require(isinstance(name, str) and name.endswith(".rgp")
             and name not in (".rgp", "..rgp") and "/" not in name
             and "\\" not in name and "\x00" not in name and not name.startswith("."),
             "UNSAFE_PATH", "metadata artifact must be a sibling RGP basename")
    _require(isinstance(meta["artifact_sha256"], str)
             and SHA256.fullmatch(meta["artifact_sha256"]) is not None,
             "UNSUPPORTED_FORMAT", "invalid RGP SHA-256")
    _require(type(meta["artifact_bytes"]) is int and 0 < meta["artifact_bytes"] <= 128 * 1024 * 1024,
             "RESOURCE_LIMIT", "invalid RGP size")
    _require(isinstance(meta["source_commit"], str)
             and COMMIT.fullmatch(meta["source_commit"]) is not None,
             "UNSUPPORTED_FORMAT", "invalid source commit")
    _require(type(meta["snapshot_complete"]) is bool,
             "UNSUPPORTED_FORMAT", "invalid snapshot coverage")
    _require(isinstance(meta["instructions"], str) and 0 < len(meta["instructions"]) <= 8192,
             "UNSUPPORTED_FORMAT", "invalid guide")
    return meta


def validate(metadata_path: Path, artifact_path: Path | None = None,
             expected_commit: str | None = None) -> dict:
    metadata_path = Path(metadata_path)
    meta = read_metadata(metadata_path)
    artifact = metadata_path.with_name(meta["artifact"])
    if artifact_path is not None:
        supplied = Path(artifact_path)
        _require(supplied.resolve() == artifact.resolve(), "UNSAFE_PATH",
                 "artifact is not the sidecar's sibling")
        artifact = supplied
    _require(artifact.is_file() and not artifact.is_symlink(), "UNSAFE_PATH", "missing regular RGP")
    _require(artifact.stat().st_size == meta["artifact_bytes"],
             "HASH_MISMATCH", "RGP size differs from sidecar")
    hasher = hashlib.sha256()
    with artifact.open("rb") as handle:
        while chunk := handle.read(65536):
            hasher.update(chunk)
    _require(hasher.hexdigest() == meta["artifact_sha256"],
             "HASH_MISMATCH", "RGP bytes differ from sidecar")
    with artifact.open("rb") as handle:
        _require(handle.readline() == b"RGP/1.2\n",
                 "UNSUPPORTED_FORMAT", "RGP transport disagrees with metadata")
    reader = Reader(artifact)
    overview = reader.overview()
    _require(overview["source_commit"] == meta["source_commit"],
             "HASH_MISMATCH", "RGP source commit differs from sidecar")
    _require(overview["snapshot_complete"] == meta["snapshot_complete"],
             "HASH_MISMATCH", "RGP coverage differs from sidecar")
    if expected_commit is not None:
        _require(expected_commit == meta["source_commit"],
                 "STALE_SNAPSHOT", "source commit differs from trusted expected revision")
    return {
        "valid": True, "artifact": meta["artifact"],
        "sha256": meta["artifact_sha256"], "source_commit": meta["source_commit"],
        "snapshot_complete": meta["snapshot_complete"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--expected-commit",
                        help="Commit from independent trusted Git provenance (not the sidecar)")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.metadata, args.artifact, args.expected_commit), indent=2))
    except (RgpError, OSError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()

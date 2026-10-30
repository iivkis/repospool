#!/usr/bin/env python3
"""Export independently authenticated RGP source to a safe local sandbox workspace.

Everything under the output directory is inert *data*: no symlinks,
no executable bits, no source imports or shell commands. Use the verified
snapshot for context; edit the actual GitHub repository via separate tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from rgp_reader import Reader, RgpError, _require, _safe_prefix

DEFAULT_MAX_FILES = 1000
DEFAULT_MAX_BYTES = 32 * 1024 * 1024
MAX_EXPORT_BYTES = 128 * 1024 * 1024
METADATA = ".rgp-sandbox.json"
HEX = re.compile(r"[0-9a-f]{64}\Z")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def export_workspace(artifact: Path, output: Path, trusted_sha256: str, *,
                     prefix: str = "", allow_partial: bool = False,
                     max_files: int = DEFAULT_MAX_FILES,
                     max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """Write a complete source selection only after authenticating the RGP.

    The output must not exist. A private staging directory protects against
    partially exported workspaces on failure; permissions deny execution.
    """
    _require(isinstance(trusted_sha256, str) and bool(HEX.fullmatch(trusted_sha256)),
             "UNSUPPORTED_FORMAT", "a separate trusted SHA-256 is mandatory")
    _require(_safe_prefix(prefix), "UNSAFE_PATH", "invalid path prefix")
    _require(type(max_files) is int and 1 <= max_files <= 50_000,
             "RESOURCE_LIMIT", "invalid file limit")
    _require(type(max_bytes) is int and 1 <= max_bytes <= MAX_EXPORT_BYTES,
             "RESOURCE_LIMIT", "invalid export byte limit")
    _require(type(allow_partial) is bool, "UNSUPPORTED_FORMAT", "allow_partial must be boolean")
    artifact = Path(artifact)
    destination = Path(output)
    _require(destination.name not in ("", ".", ".."),
             "UNSAFE_PATH", "invalid output directory")
    _require(not destination.exists() and not destination.is_symlink(),
             "UNSAFE_PATH", "output path already exists")
    _require(artifact.is_file() and not artifact.is_symlink(),
             "UNSAFE_PATH", "artifact must be a local regular file")
    observed = _sha256(artifact)
    _require(observed == trusted_sha256, "HASH_MISMATCH",
             "RGP does not match externally trusted SHA-256")
    reader = Reader(artifact)
    overview = reader.overview()
    _require(overview["snapshot_complete"] or allow_partial, "PARTIAL_SNAPSHOT",
             "snapshot is incomplete; explicit allow_partial required")
    entries = [e for e in reader.manifest["files"] if e["path"].startswith(prefix)]
    _require(len(entries) <= max_files, "RESOURCE_LIMIT",
             "workspace exceeds max_files; choose a prefix or raise the limit")
    total = sum(e["size"] for e in entries)
    _require(total <= max_bytes, "RESOURCE_LIMIT",
             "workspace exceeds max_bytes; choose a prefix or raise the limit")
    _require(METADATA not in {e["path"] for e in entries}, "UNSAFE_PATH",
             "source collides with reserved workspace metadata path")
    parent = destination.parent
    _require(parent.is_dir(), "UNSAFE_PATH", "output parent directory missing")
    staging = Path(tempfile.mkdtemp(prefix=".rgp-stage-", dir=parent))
    try:
        pointer_paths, symlink_text_paths = [], []
        for entry in entries:
            path = staging.joinpath(*entry["path"].split("/"))
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = reader._blob(entry["path"])  # Reader verified these bytes before export.
            _require(len(data) == entry["size"] and hashlib.sha256(data).hexdigest() == entry["sha256"],
                     "HASH_MISMATCH", "source blob changed during workspace export")
            # O_NOFOLLOW is defense-in-depth. The private staging tree contains
            # only directories and newly created ordinary files, never symlinks.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as destination_file:
                destination_file.write(data)
            if entry["status"] == "pointer_only":
                pointer_paths.append(entry["path"])
            if entry["kind"] == "symlink":
                symlink_text_paths.append(entry["path"])
        report = {
            "kind": "rgp_sandbox_workspace",
            "source_commit": overview["source_commit"],
            "input_tree_digest": overview["input_tree_digest"],
            "rgp_sha256": observed,
            "snapshot_complete": overview["snapshot_complete"],
            "snapshot_status": overview["status"],
            "prefix": prefix,
            "files_exported": len(entries),
            "bytes_exported": total,
            "unavailable_paths": overview["excluded_files"],
            "pointer_only_paths": pointer_paths,
            "symlinks_materialized_as_regular_text_files": symlink_text_paths,
            "files_non_executable": True,
            "source_instructions_untrusted": True,
            "git_worktree": False,
        }
        (staging / METADATA).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        os.chmod(staging / METADATA, 0o600)
        # A preexisting output is never silently replaced or merged.
        _require(not destination.exists() and not destination.is_symlink(),
                 "UNSAFE_PATH", "output path was created during export")
        os.rename(staging, destination)
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--trusted-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args()
    try:
        result = export_workspace(
            args.artifact, args.output, args.trusted_sha256,
            prefix=args.prefix, allow_partial=args.allow_partial,
            max_files=args.max_files, max_bytes=args.max_bytes)
    except (RgpError, OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

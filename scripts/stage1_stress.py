#!/usr/bin/env python3
"""Stage 1 synthetic stress and resource-boundary acceptance (stdlib only).

This creates a temporary, reproducible Git repository from test data.
No source archive or synthetic large blob is committed or uploaded.
All assertions use the *actual generated RGP* and independent Git bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
from rgp_reader import Reader, RgpError, decode_transport  # noqa: E402
from stage1_corpus import measured  # noqa: E402

MIB = 1024 * 1024
PROFILES = ("many-files", "incompressible", "compressible", "disk-fallback", "oversized")


def insist(ok: bool, detail: str) -> None:
    if not ok:
        raise AssertionError(detail)


def call(*args: str, env: dict | None = None) -> bytes:
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, env=env)
    if result.returncode:
        raise RuntimeError(f"command {args[:3]!r} exited {result.returncode}: "
                           f"{result.stderr[-600:].decode(errors='replace')}")
    return result.stdout


def make_source(repo: Path, profile: str) -> tuple[str, dict[str, bytes]]:
    insist(profile in PROFILES, "unrecognized stress profile")
    call("git", "init", "-q", str(repo))
    originals: dict[str, bytes] = {}

    def noise(seed: bytes, size: int) -> bytes:
        # Deterministic ASCII high-entropy data, not binary ZIP fixtures.
        return hashlib.shake_256(seed).digest(size // 2 + size % 2).hex().encode()[:size]

    if profile == "many-files":
        for index in range(2000):
            path = f"src/group-{index // 200:02d}/module-{index:04d}.txt"
            data = noise(f"RGP-stress-file-{index}".encode(), 1024)
            originals[path] = data
    elif profile == "incompressible":
        originals["data/incompressible.txt"] = noise(
            b"RGP-incompressible-12MiB", 12 * MIB)
    elif profile == "compressible":
        originals["data/compressible.txt"] = (b"A" * 4095 + bytes([10])) * 3072
    elif profile == "disk-fallback":
        # 36 MiB of readable high-entropy source exercises disk staging.
        for index in range(3):
            originals[f"data/disk-{index}.txt"] = noise(
                f"RGP-disk-fallback-{index}".encode(), 12 * MIB)
    else:
        # Exceed the documented per-file cap by one byte: must be EXCLUDED,
        # not mislabeled as a complete snapshot or a successful read.
        originals["data/oversized.bin"] = b"Z" * (16 * MIB + 1)
        originals["README.md"] = b"visible source stays available\n"

    for relative, data in originals.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    call("git", "-C", str(repo), "add", "--", ".")
    environment = dict(os.environ,
                       GIT_AUTHOR_DATE="2020-01-01T00:00:00+00:00",
                       GIT_COMMITTER_DATE="2020-01-01T00:00:00+00:00",
                       TZ="UTC")
    call("git", "-C", str(repo),
         "-c", "user.name=RGP Stress",
         "-c", "user.email=rgp-stress@example.invalid",
         "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", f"stress {profile}", env=environment)
    commit = call("git", "-C", str(repo), "rev-parse", "HEAD").decode().strip()
    return commit, originals


def run(profile: str, binary: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"rgp-stress-{profile}-") as folder:
        workspace = Path(folder)
        repo = workspace / "source"
        commit, source = make_source(repo, profile)
        first, second = workspace / "first.rgp", workspace / "second.rgp"
        base = [str(binary), "generate", "--repo", str(repo), "--rev", commit]
        first_metrics = measured([*base, "--output", str(first)], workspace / "gen1.txt")
        second_metrics = measured([*base, "--output", str(second)], workspace / "gen2.txt")
        first_bytes = first.read_bytes()
        insist(first_bytes == second.read_bytes(), "non-deterministic stress RGP")
        verify_metrics = measured([str(binary), "verify", "--artifact", str(first),
                                   "--repo", str(repo)], workspace / "verify.txt")
        start = time.perf_counter_ns()
        reader = Reader(first)
        reader_validation_ms = round((time.perf_counter_ns() - start) / 1e6, 3)
        overview = reader.overview()
        insist(overview["source_commit"] == commit and reader.validate()["valid"],
               "source revision or RGP integrity mismatch")
        insist(first_bytes.startswith(b"RGP/2.1\n"), "expected readable transport")
        insist(b"\nFILE\t" in first_bytes, "missing plaintext file sections")

        observed_source_sha = {}
        for path, expected in source.items():
            independent = call("git", "-C", str(repo), "show", f"{commit}:{path}")
            insist(expected == independent, f"fixture Git source mismatch: {path}")
            if profile == "oversized" and path == "data/oversized.bin":
                try:
                    reader.read(path)
                except RgpError as exc:
                    insist(exc.code == "PARTIAL_SNAPSHOT", "oversized file wrong error")
                else:
                    raise AssertionError("oversized source unexpectedly readable")
                continue
            actual = reader._blob(path)  # Private helper used for byte-fidelity only.
            insist(actual == independent, f"RGP bytes differ from Git: {path}")
            observed_source_sha[path] = hashlib.sha256(actual).hexdigest()
            if path.endswith(".txt") and profile == "compressible":
                found = reader.search("AAAA", paths=[path], limit=1)
                insist(found["matches"][0]["line"] == 1, "large-text search failed")
                insist(reader.read(path, 1, 1)["content"] == expected.splitlines(keepends=True)[0].decode(),
                       "line reading lost large source bytes")
        if profile == "many-files":
            paths = set()
            cursor = None
            while True:
                page = reader.tree(limit=79, cursor=cursor)
                insist(not paths.intersection(page["paths"]), "pagination duplicate")
                paths.update(page["paths"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            insist(paths == set(source), "many-file pagination lost paths")
        if profile == "compressible":
            insist(overview["snapshot_complete"], "compressible valid source marked partial")
        if profile in ("incompressible", "disk-fallback"):
            insist(overview["snapshot_complete"], "incompressible valid source marked partial")
        if profile == "disk-fallback":
            insist(overview["file_count"] == 3, "disk fallback lost source files")
            insist(len(first_bytes) > 32 * MIB, "did not exercise large plaintext transport")
        if profile == "oversized":
            insist(not overview["snapshot_complete"], "oversized source misrepresented as complete")
            insist(overview["excluded_by_reason"].get("size_limit") == 1,
                   "oversized source missing explicit exclusion")
        if profile == "many-files":
            insist(overview["file_count"] == 2000 and overview["snapshot_complete"],
                   "many-file snapshot incomplete")
        # Fail closed if the real generated artifact is truncated.
        try:
            Reader(first_bytes[:-10])
        except RgpError as exc:
            insist(exc.code == "TRUNCATED_ARTIFACT", "unexpected truncation result")
        else:
            raise AssertionError("truncated stress RGP accepted")

        return {
            "kind": "stage1_synthetic_stress_not_external_benchmark",
            "status": "PASS", "profile": profile, "source_commit": commit,
            "rgp_sha256": hashlib.sha256(first_bytes).hexdigest(),
            "rgp_bytes": len(first_bytes), "file_count": overview["file_count"],
            "excluded_by_reason": overview["excluded_by_reason"],
            "snapshot_complete": overview["snapshot_complete"],
            "stored_blob_count": 0,
            "verified_included_source_count": len(observed_source_sha),
            "timings": {"generate_first": first_metrics,
                        "generate_second": second_metrics,
                        "verify": verify_metrics,
                        "reader_validate_ms": reader_validation_ms},
            "limits": {"per_file_bytes": 16 * MIB, "max_inflated_bytes": 128 * MIB},
            "gate_b": "HOLD", "notes": [
                "local generated Git fixture, no public repository data",
                "no measured GitHub connector transport, inference tokens or real-world speedup",
                "GNU time wall-time resolution is 10 ms; cache and host may vary",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--rust-cli", type=Path, default=ROOT / "target/release/rgp")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.profile, args.rust_cli.resolve())
    except Exception as exc:
        report = {"kind": "stage1_synthetic_stress_not_external_benchmark",
                  "status": "FAIL", "profile": args.profile,
                  "reason": str(exc), "gate_b": "HOLD"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

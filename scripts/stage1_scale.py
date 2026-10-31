#!/usr/bin/env python3
"""Stage 1: 50,000-member RGP boundary from a real synthetic Git commit.

Generates 50,005 distinct Git blobs using git fast-import (no filesystem
checkout), tests the bounded 50,000-file snapshot boundary, verifies exact
source reads, pagination, deterministic bytes and honest exclusions.
CI artifacts contain JSON measurements only. No model or third-party Python.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
from rgp_reader import Reader, RgpError  # noqa: E402
from stage1_corpus import measured  # noqa: E402

FILES = 50_005
CAP = 49_999
SAMPLE = (0, 9_999, 25_000, CAP - 1)


def demand(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def path_for(index: int) -> str:
    return f"src/scale/file-{index:05d}.txt"


def content_for(index: int) -> bytes:
    return f"source_{index:05d} = exactly_pinned_git_blob_{index:05d}\n".encode()


def commit_many(repo: Path, count: int) -> str:
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    process = subprocess.Popen(
        ["git", "-C", str(repo), "fast-import", "--quiet"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert process.stdin is not None
        stream = process.stdin
        # Deliberately stream every object; do not build a 50k-file
        # working tree or hold all source bytes in the Python harness.
        for index in range(count):
            data = content_for(index)
            stream.write(f"blob\nmark :{index + 1}\ndata {len(data)}\n".encode())
            stream.write(data)
            stream.write(b"\n")
        stream.write(b"commit refs/heads/main\n")
        stream.write(
            b"author RGP Scale <rgp-scale@example.invalid> 1577836800 +0000\n"
            b"committer RGP Scale <rgp-scale@example.invalid> 1577836800 +0000\n"
            b"data 20\nstage1 scale fixture\n"
        )
        stream.write(b"deleteall\n")
        for index in range(count):
            stream.write(f"M 100644 :{index + 1} {path_for(index)}\n".encode())
        stream.write(b"\n")
        stream.close()
        assert process.stderr is not None
        stderr = process.stderr.read()
        assert process.stdout is not None
        stdout = process.stdout.read()
        result = process.wait()
        demand(result == 0, f"git fast-import failed: {stderr[-800:]!r}, {stdout[-200:]!r}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    # A new Git repository may have HEAD on master while fast-import
    # committed refs/heads/main. Rust verify --repo checks HEAD.
    subprocess.run(["git", "-C", str(repo), "symbolic-ref",
                    "HEAD", "refs/heads/main"], check=True)
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "refs/heads/main"], text=True
    ).strip()


def run(binary: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="rgp-stage1-50k-") as folder:
        root = Path(folder)
        repo = root / "source"
        commit = commit_many(repo, FILES)
        first, second = root / "first.rgp", root / "second.rgp"
        cmd = [str(binary), "generate", "--repo", str(repo),
               "--rev", commit]
        gen_first = measured([*cmd, "--output", str(first)], root / "first.time")
        gen_second = measured([*cmd, "--output", str(second)], root / "second.time")
        raw = first.read_bytes()
        demand(raw == second.read_bytes(), "large Git snapshot not deterministic")
        verification = measured([str(binary), "verify", "--artifact", str(first),
                                 "--repo", str(repo)], root / "verify.time")
        start = time.perf_counter_ns()
        reader = Reader(first)
        reader_ms = round((time.perf_counter_ns() - start) / 1e6, 3)
        summary = reader.overview()
        demand(reader.validate()["valid"], "Reader validation failed")
        demand(summary["source_commit"] is None, "RGP transport must omit commit ID")
        demand(summary["file_count"] == CAP, "file inclusion exceeded capacity")
        demand(not summary["snapshot_complete"], "capacity truncation claimed complete")
        demand(summary["excluded_by_reason"].get("entry_limit") == FILES - CAP,
               "entry-limit exclusions not disclosed")
        demand(summary["index_coverage"] == "not_indexed",
               "index coverage hallucinated at scale")
        demand(raw.startswith(b"RGP/0.1.0\n"), "expected raw UTF-8 source transport")
        source_sections = len(reader._files)
        demand(source_sections == CAP, "plaintext source section limit")
        # All paths are covered by the public paginated tree operation,
        # not by inspecting the Reader's private path map.
        pages = 0
        count = 0
        cursor = None
        previous = None
        pagination_started = time.perf_counter_ns()
        while True:
            page = reader.tree(limit=200, cursor=cursor)
            paths = page["paths"]
            demand(bool(paths) and paths == sorted(paths), "tree pages out of order")
            demand(previous is None or previous < paths[0], "overlapping pagination pages")
            previous = paths[-1]
            count += len(paths)
            pages += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
            demand(pages <= 251, "cursor failed to terminate")
        pagination_ms = round((time.perf_counter_ns() - pagination_started) / 1e6, 3)
        demand(count == CAP and pages == 250, "tree enumeration incomplete")
        for index in SAMPLE:
            path = path_for(index)
            expected = subprocess.check_output(
                ["git", "-C", str(repo), "show", f"{commit}:{path}"])
            demand(reader.read(path, 1, 1)["content"].encode() == expected,
                   f"read mismatch against pinned Git: {path}")
            needle = f"exactly_pinned_git_blob_{index:05d}"
            found = reader.search(needle, paths=[path], limit=1)
            demand([m["line"] for m in found["matches"]] == [1]
                   and found["next_cursor"] is None,
                   f"search failed against pinned Git: {path}")
        excluded = path_for(FILES - 1)
        demand(subprocess.check_output([
            "git", "-C", str(repo), "show", f"{commit}:{excluded}"]) == content_for(FILES - 1),
            "excluded source was absent from original Git")
        for index in (CAP, FILES - 1):
            try:
                reader.read(path_for(index))
            except RgpError as exc:
                demand(exc.code == "PARTIAL_SNAPSHOT", "excluded read wrong error")
            else:
                raise AssertionError("excluded source was readable")
        try:
            Reader(raw[:-10])
        except RgpError as exc:
            demand(exc.code == "TRUNCATED_ARTIFACT", "unexpected truncation rejection")
        else:
            raise AssertionError("truncated large artifact accepted")

        return {
            "kind": "stage1_synthetic_git_50k_member_acceptance",
            "status": "PASS", "source_commit": commit,
            "source_files": FILES, "included_files": CAP,
            "excluded_files": FILES - CAP,
            "plain_source_sections": source_sections,
            "rgp_sha256": hashlib.sha256(raw).hexdigest(),
            "rgp_bytes": len(raw), "pagination_pages": pages,
            "sampled_git_source_checks": len(SAMPLE),
            "timings": {"generation_first": gen_first,
                        "generation_second": gen_second,
                        "rust_verification": verification,
                        "reader_validation_ms": reader_ms,
                        "full_pagination_ms": pagination_ms},
            "harness_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "gate_b": "HOLD",
            "not_measured": ["GitHub connector latency", "agent inference tokens",
                             "independent natural-language grading"],
            "note": "Synthetic pinned Git commit; text section integrity checked by Rust and Python; partial snapshot is deliberate at member cap.",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-cli", type=Path, default=ROOT / "target/release/rgp")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.rust_cli.resolve())
    except Exception as exc:
        report = {"kind": "stage1_synthetic_git_50k_member_acceptance",
                  "status": "FAIL", "reason": str(exc), "gate_b": "HOLD"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return int(report["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())

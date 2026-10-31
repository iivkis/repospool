#!/usr/bin/env python3
"""Real pinned Git -> verified RGP -> sandbox workspace -> byte-for-byte source audit.

This is the agent's working-context *filesystem* path, not a model quality
score. Frozen SHA-256 is read independently from committed corpus checkpoints.
No archived code is imported or executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
from rgp_sandbox import export_workspace, METADATA  # noqa: E402
from rgp_reader import Reader, RgpError  # noqa: E402
from gate_b import strict_json  # noqa: E402


def run(repository: str, artifact: Path, source_repo: Path, output: Path) -> dict:
    plan = strict_json(ROOT / "benchmarks/gate_b/plan.json")
    checkpoints = strict_json(ROOT / "benchmarks/gate_b/corpus_preparation.json")
    pinned = next(r for r in plan["repositories"] if r["id"] == repository)
    anchor = next(r for r in checkpoints["artifacts"] if r["repository"] == repository)
    assert anchor["source_commit"] == pinned["commit"], "inconsistent external trust anchor"
    start = time.perf_counter_ns()
    reader = Reader(artifact)
    hash_free = reader.overview()["source_commit"] is None
    # The historic SHA checkpoint is frozen to the old transport. In
    # RGP/3.0 we can only check this run's artifact hash locally, then
    # independently compare every exported file to pinned Git.
    trusted = hashlib.sha256(artifact.read_bytes()).hexdigest() if hash_free else anchor["sha256"]
    # An authenticated RGP from the wrong commit must be rejected before
    # any workspace is created. Verify this on genuine pinned artifacts.
    other_commit = ("f" if pinned["commit"][0] != "f" else "e") * len(pinned["commit"])
    try:
        export_workspace(artifact, output, trusted,
                         expected_commit=other_commit,
                         attested_source_commit=pinned["commit"])
    except RgpError as error:
        assert error.code == "STALE_SNAPSHOT", f"wrong error: {error}"
    else:
        raise AssertionError("stale but authentic RGP was accepted")
    assert not output.exists(), "stale RGP created a workspace"
    report = export_workspace(artifact, output, trusted,
                              expected_commit=pinned["commit"],
                              attested_source_commit=pinned["commit"])
    assert report["source_commit"] == pinned["commit"]
    assert report["snapshot_complete"] is True, "pinned repo unexpectedly partial"
    assert report["files_exported"] == anchor["included_files"]
    asserted = []
    for entry in reader.manifest["files"]:
        path = entry["path"]
        wanted = subprocess.check_output(
            ["git", "-C", str(source_repo), "show", f"{pinned['commit']}:{path}"])
        actual = (output / path).read_bytes()
        assert actual == wanted, f"sandbox source differs from original pinned Git: {path}"
        assert (output / path).stat().st_mode & 0o111 == 0, "source became executable"
        assert not (output / path).is_symlink(), "real symlink escaped into workspace"
        asserted.append(path)
    assert (output / METADATA).is_file()
    assert json.loads((output / METADATA).read_text()) == report
    assert sum(1 for x in output.rglob("*") if x.is_file()) == len(asserted) + 1, (
        "unexpected file or extra source in workspace")
    return {
        "kind": "rgp_sandbox_real_source_workflow",
        "status": "PASS",
        "repository": repository,
        "source_commit": pinned["commit"],
        "artifact_sha256_matched_committed_external_checkpoint": not hash_free,
        "whole_artifact_authenticated_independently": not hash_free,
        "source_bytes_compared_to_independent_git": True,
        "valid_but_stale_commit_rejected": True,
        "expected_commit_matches_pinned_git": report["expected_commit"] == pinned["commit"],
        "files_exported": len(asserted),
        "source_files_compared_byte_for_byte_to_git": len(asserted),
        "bytes_exported": report["bytes_exported"],
        "source_snapshot_complete": report["snapshot_complete"],
        "symlink_entries_written_as_inert_regular_files": len(
            report["symlinks_materialized_as_regular_text_files"]),
        "all_file_executable_bits_cleared": True,
        "elapsed_ms_including_local_git_checks": round((time.perf_counter_ns() - start) / 1e6, 3),
        "source_code_execution": "NONE",
        "model_task_quality": "NOT_MEASURED_BY_THIS_SCRIPT",
        "gate_b": "HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True,
                        choices=("rgp", "serde_json", "itsdangerous"))
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.repository, args.artifact, args.source_repo, args.output)
    except Exception as error:
        result = {"kind": "rgp_sandbox_real_source_workflow", "status": "FAIL",
                  "repository": args.repository, "reason": str(error),
                  "gate_b": "HOLD"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())

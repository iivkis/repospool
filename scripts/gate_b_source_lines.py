#!/usr/bin/env python3
"""Recompute the Gate B source-line index from three verified pinned RGP files.

Uses only the trusted Python stdlib Reader. No source code from the snapshots
is executed or extracted to the filesystem.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from rgp_reader import Reader, RgpError  # noqa: E402
from gate_b import ProtocolError, digest_plan, require, strict_json, validate_plan  # noqa: E402
from gate_b_review import validate_source_line_counts  # noqa: E402


def make_source_line_index(plan: dict, observations: dict, checkpoint: dict,
                           rgp_dir: Path) -> dict:
    validate_plan(plan)
    require(isinstance(checkpoint, dict) and isinstance(checkpoint.get("artifacts"), list),
            "missing RGP corpus checkpoint")
    by_repo = {a["repository"]: a for a in checkpoint["artifacts"]
               if isinstance(a, dict) and isinstance(a.get("repository"), str)}
    repos = {repo["id"]: repo for repo in plan["repositories"]}
    require(set(by_repo) == set(repos), "RGP checkpoint repositories do not match plan")
    row_index = {(r["task_id"], r["arm"]): r for r in observations["observations"]}
    entries = []
    seen = set()
    for repo_id, repo in repos.items():
        raw = (rgp_dir / (repo_id + ".rgp")).read_bytes()
        artifact = by_repo[repo_id]
        require(hashlib.sha256(raw).hexdigest() == artifact["sha256"],
                f"{repo_id}: RGP SHA-256 checkpoint mismatch")
        require(len(raw) == artifact["rgp_bytes"], f"{repo_id}: RGP byte size mismatch")
        reader = Reader(raw)
        summary = reader.overview()
        require(summary["snapshot_complete"] is True and
                summary["source_commit"] == repo["commit"] and
                summary["included_files"] == artifact["included_files"],
                f"{repo_id}: incomplete/wrong pinned snapshot")
        for task in plan["tasks"]:
            if task["repository"] != repo_id:
                continue
            for path in task["reference_paths"]:
                key = (repo_id, path)
                if key in seen:
                    continue
                seen.add(key)
                response = reader.read(path)
                require(response["status"] == "included" and response["encoding"] == "utf-8",
                        f"{repo_id}/{path}: source must be fully included UTF-8")
                raw_blob = response["content"].encode("utf-8")
                git_sha = hashlib.sha1(
                    b"blob " + str(len(raw_blob)).encode("ascii") + b"\0" + raw_blob
                ).hexdigest()
                original = row_index[(task["id"], "github_connector")]["source_evidence"][path]
                require(git_sha == original["git_blob_sha1"],
                        f"{repo_id}/{path}: Git blob SHA-1 mismatch")
                line_count = raw_blob.count(b"\n") + int(not raw_blob.endswith(b"\n"))
                require(line_count > 0, f"{repo_id}/{path}: empty source")
                entries.append({
                    "repository": repo_id,
                    "source_commit": repo["commit"],
                    "path": path,
                    "git_blob_sha1": git_sha,
                    "line_count": line_count,
                })
    result = {
        "schema_version": "1.0",
        "plan_sha256": digest_plan(plan),
        "method": "verified_pinned_rgp_reader_plus_git_blob_sha1",
        "source_rgp_sha256": {key: by_repo[key]["sha256"] for key in by_repo},
        "files": entries,
    }
    validate_source_line_counts(plan, observations, result, checkpoint)
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rgp-dir", type=Path, required=True)
    p.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    p.add_argument("--observations", type=Path, default=Path(
        "benchmarks/gate_b/observations.cache_fair_2026-10-29.json"))
    p.add_argument("--checkpoint", type=Path, default=Path(
        "benchmarks/gate_b/corpus_preparation.json"))
    p.add_argument("--expected", type=Path, default=Path(
        "benchmarks/gate_b/source_line_counts.json"))
    p.add_argument("--check", action="store_true",
                   help="Fail when generated data differs from the committed source index")
    args = p.parse_args()
    try:
        manifest = make_source_line_index(strict_json(args.plan),
                                          strict_json(args.observations),
                                          strict_json(args.checkpoint),
                                          args.rgp_dir)
        if args.check:
            require(manifest == strict_json(args.expected),
                    "source-line index changed from pinned RGP bytes")
            print(json.dumps({"status": "PASS", "files": len(manifest["files"]),
                              "verified_snapshots": 3}, sort_keys=True))
        else:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except (ProtocolError, RgpError, OSError, ValueError, TypeError, KeyError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Stage 1 delivery acceptance for a GitHub Actions ZIP.

A *committed*, separately retrieved corpus checkpoint is the expected RGP
SHA-256; hashes inside a ZIP are not trusted anchors. All bytes are verified
by the existing stdlib bootstrap and RGP Reader. Optional --source-repo
independently checks every frozen task reference against pinned Git objects.

This tool cannot attest which connector returned the ZIP, authenticate
reviewer independence, or grade natural-language Gate B answers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_bootstrap import bootstrap  # noqa: E402
from rgp_reader import Reader, RgpError  # noqa: E402
from gate_b import ProtocolError, digest_plan, strict_json, validate_plan  # noqa: E402
from stage1_corpus import ANCHORS  # noqa: E402


def insist(condition: bool, detail: str) -> None:
    if not condition:
        raise ProtocolError(detail)


def pinned_expectation(plan: dict, checkpoint: dict, name: str) -> tuple[dict, dict, list[dict]]:
    validate_plan(plan)
    repos = [repo for repo in plan["repositories"] if repo["id"] == name]
    rows = checkpoint.get("artifacts")
    insist(isinstance(rows, list), "missing corpus checkpoint")
    matches = [row for row in rows if isinstance(row, dict)
               and row.get("repository") == name]
    insist(len(repos) == 1 and len(matches) == 1, "unknown or duplicate repository checkpoint")
    repo, row = repos[0], matches[0]
    insist(repo["commit"] == row.get("source_commit"), "checkpoint source revision differs from frozen plan")
    insist(isinstance(row.get("sha256"), str) and len(row["sha256"]) == 64
           and all(c in "0123456789abcdef" for c in row["sha256"]),
           "invalid checkpoint digest")
    insist(type(row.get("rgp_bytes")) is int and row["rgp_bytes"] > 0
           and type(row.get("included_files")) is int and row["included_files"] >= 0,
           "invalid checkpoint sizes")
    tasks = [task for task in plan["tasks"] if task["repository"] == name]
    insist(bool(tasks), "no pinned task coverage")
    return repo, row, tasks


def original_blob(repo_path: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo_path), "show", f"{commit}:{path}"],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout


def verify_delivered(zip_path: Path, output: Path, repository: str,
                     plan: dict, checkpoint: dict,
                     source_repo: Path | None = None) -> dict:
    repo, row, tasks = pinned_expectation(plan, checkpoint, repository)
    # Hash/size are read from the independently committed repository record,
    # NOT from the ZIP's optional co-packed provenance.json.
    result = bootstrap(zip_path, output, trusted_sha256=row["sha256"])
    artifact = output.read_bytes()
    insist(result["sha256"] == row["sha256"] and
           len(artifact) == row["rgp_bytes"], "delivered artifact differs from checkpoint")
    reader = Reader(output)
    overview = reader.overview()
    insist(overview["source_commit"] == repo["commit"], "delivered source commit mismatch")
    insist(overview["snapshot_complete"], "delivered RGP is a partial snapshot")
    insist(overview["file_count"] == row["included_files"], "delivered file count differs")
    insist(reader.coverage("index")["status"] == "not_indexed", "unearned index coverage")

    found = set()
    cursor = None
    while True:
        page = reader.tree(limit=25, cursor=cursor)
        insist(not (found & set(page["paths"])), "duplicate delivered tree path")
        found.update(page["paths"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    insist(len(found) == overview["file_count"], "delivered tree is incomplete")
    seen: dict[str, bytes] = {}
    verified = []
    for task in tasks:
        insist(task["id"] in ANCHORS and
               set(ANCHORS[task["id"]]) == set(task["reference_paths"]),
               "missing preregistered source-navigation anchors")
        for path in task["reference_paths"]:
            insist(path in found, f"missing delivered reference {task['id']}:{path}")
            if source_repo is not None:
                if path not in seen:
                    seen[path] = original_blob(source_repo, repo["commit"], path)
                original = seen[path]
                insist(reader._blob(path) == original, f"delivered bytes differ from Git: {path}")
                lines = original.decode("utf-8").splitlines(keepends=True)
                for needle in ANCHORS[task["id"]][path]:
                    expected = [n for n, line in enumerate(lines, 1) if needle in line]
                    insist(0 < len(expected) < 100, "missing/ambiguous predefined anchor")
                    actual = reader.search(needle, paths=[path], limit=100)
                    insist([match["line"] for match in actual["matches"]] == expected and
                           actual["next_cursor"] is None,
                           f"delivered search does not match Git: {task['id']}:{path}")
                    line = expected[0]
                    insist(reader.read(path, line, line)["content"] == lines[line - 1],
                           f"delivered read differs from Git: {task['id']}:{path}")
        verified.append(task["id"])
    return {
        "kind": "stage1_github_actions_zip_delivery",
        "status": "PASS", "repository": repository,
        "source_commit": repo["commit"],
        "plan_sha256": digest_plan(plan),
        "rgp_sha256": hashlib.sha256(artifact).hexdigest(),
        "rgp_bytes": len(artifact),
        "snapshot_complete": overview["snapshot_complete"],
        "task_reference_sets": len(verified), "task_ids": verified,
        "unique_git_blobs_compared": len(seen),
        "validation": ["external committed SHA256 checkpoint",
                       "strict Actions ZIP bootstrap", "RGP 1.0 stdlib Reader",
                       "full tree pagination",
                       "all frozen task reference paths"] +
                      (["read/search and bytes independently compared with pinned Git"]
                       if source_repo is not None else []),
        "source_comparison": "PASS" if source_repo is not None else "NOT_RUN",
        "connector_acquisition": "NOT_ATTESTED_BY_LOCAL_SCRIPT",
        "gate_b": "HOLD",
        "not_measured": ["authenticated connected-tool call history",
                         "independent answer grading", "model token usage"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actions-zip", type=Path, required=True)
    parser.add_argument("--repository", required=True, choices=("rgp", "serde_json", "itsdangerous"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--plan", type=Path, default=ROOT / "benchmarks/gate_b/plan.json")
    parser.add_argument("--checkpoint", type=Path,
                        default=ROOT / "benchmarks/gate_b/corpus_preparation.json")
    args = parser.parse_args()
    try:
        result = verify_delivered(args.actions_zip, args.output, args.repository,
                                  strict_json(args.plan), strict_json(args.checkpoint),
                                  args.source_repo)
    except (ProtocolError, RgpError, ValueError, OSError,
            subprocess.CalledProcessError, KeyError) as exc:
        result = {"kind": "stage1_github_actions_zip_delivery",
                  "status": "FAIL", "repository": args.repository,
                  "reason": str(exc), "gate_b": "HOLD"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

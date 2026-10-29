#!/usr/bin/env python3
"""Byte-level RGP and reference-path verification. No model invocation.

This checks source availability and snapshot integrity, *not* task correctness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from rgp_reader import Reader  # noqa: E402
from gate_b import ProtocolError, digest_plan, strict_json, validate_plan


def audit(plan: dict, corpus: dict, directory: Path) -> dict:
    validate_plan(plan)
    checkpoints = {a["repository"]: a for a in corpus["artifacts"]}
    out = {"kind": "source_integrity_only", "plan_sha256": digest_plan(plan),
           "gate_b_status": "HOLD", "repositories": [], "reference_sets_checked": 0}
    for repo in plan["repositories"]:
        name = repo["id"]
        if name not in checkpoints:
            raise ProtocolError(f"missing corpus checkpoint: {name}")
        checkpoint = checkpoints[name]
        if checkpoint["source_commit"] != repo["commit"]:
            raise ProtocolError(f"wrong source commit in checkpoint: {name}")
        artifact = directory / f"{name}.rgp"
        if not artifact.is_file():
            raise ProtocolError(f"missing RGP: {artifact}")
        raw = artifact.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != checkpoint["sha256"]:
            raise ProtocolError(f"RGP SHA-256 mismatch: {name}")
        reader = Reader(raw)
        if reader.manifest["source_commit"] != repo["commit"]:
            raise ProtocolError(f"artifact commit mismatch: {name}")
        if not reader.overview()["snapshot_complete"]:
            raise ProtocolError(f"partial snapshot: {name}")
        if len(reader.manifest["files"]) != checkpoint["included_files"]:
            raise ProtocolError(f"included count mismatch: {name}")
        repo_tasks = [t for t in plan["tasks"] if t["repository"] == name]
        for task in repo_tasks:
            for path in task["reference_paths"]:
                if path not in reader._files:
                    raise ProtocolError(f"missing reference {name}:{task['id']}:{path}")
            out["reference_sets_checked"] += 1
        out["repositories"].append({"id": name, "source_commit": repo["commit"],
             "rgp_sha256": sha, "rgp_bytes": len(raw),
             "included_files": len(reader.manifest["files"]),
             "task_reference_sets": len(repo_tasks), "verified": True})
    out["note"] = "Verified artifact bytes, pinned commits and referenced paths; no answers, call savings or tokens measured."
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    p.add_argument("--corpus", type=Path, default=Path("benchmarks/gate_b/corpus_preparation.json"))
    p.add_argument("--rgp-dir", required=True, type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    try:
        output = audit(strict_json(args.plan), strict_json(args.corpus), args.rgp_dir)
    except (ProtocolError, OSError, ValueError, KeyError) as exc:
        p.error(str(exc))
    data = json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8")
    print(data, end="")


if __name__ == "__main__":
    main()

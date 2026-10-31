#!/usr/bin/env python3
"""Twelve preregistered coding questions: real RGP context vs pinned Git truth.

This is a *source-grounding* trial, not a hidden model inference benchmark:
the search terms below are author-selected and deliberately informed by
the frozen tasks. Each exact excerpt is compared against independent Git
objects. Do not infer LLM accuracy, token savings or connector calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
from rgp_reader import Reader, RgpError  # noqa: E402
from rgp_agent_context import context, compact_json  # noqa: E402
from gate_b import strict_json, validate_plan  # noqa: E402

# Term selection is explicitly *not* blinded: an assistant looked at the
# frozen reference paths and knew relevant symbols. This measures whether
# RGP supplies verifiable source material, not unaided model discovery.
TERMS = {
    "rgp-transport-hash": ("payload_length=", "payload_sha256=", "ZIP hash mismatch"),
    "rgp-git-snapshot": ("fn snapshot(", "submodule_unavailable", "pointer_only"),
    "rgp-search-cursor": ("_read_cursor", "test_search_keyset_cursor_scope_and_exhaustion"),
    "rgp-trust-boundary": ("def bootstrap(", "unsafe Actions ZIP entry", "Trust boundary"),
    "serde-json-deserializer": ("pub fn from_str(", "pub fn from_slice("),
    "serde-json-serializer": ("fn serialize_i64(", "fn serialize_f64(", "fn serialize_str("),
    "serde-json-error-position": ("pub fn line(", "pub fn column(", "pub fn classify("),
    "serde-json-ser-tests": ("fn test_write_i64()", "fn test_write_f64()",
                             "fn test_write_str()", "fn test_write_list()", "fn serialize_str("),
    "itsdangerous-timestamp": ("if age > max_age:", "if age < 0:"),
    "itsdangerous-hmac": ("class HMACAlgorithm", "mac = hmac.new(",
                           "sig = self.algorithm.get_signature"),
    "itsdangerous-deserialization": ("def load_payload(",
                                    "return self.load_payload(signer.unsign(s))", "def unsign("),
    "itsdangerous-expiry-tests": ("def test_max_age(", "if age > max_age:"),
}


def git_blob(source_repo: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output([
        "git", "-C", str(source_repo), "show", f"{commit}:{path}"
    ])


def run(artifact: Path, source_repo: Path, repository: str,
        plan_file: Path = ROOT / "benchmarks/gate_b/plan.json") -> dict:
    plan = strict_json(plan_file)
    validate_plan(plan)
    expected = [task for task in plan["tasks"] if task["repository"] == repository]
    assert len(expected) == 4, "each frozen repository must have four tasks"
    assert set(TERMS) == {task["id"] for task in plan["tasks"]}, (
        "not covering the complete frozen twelve-task plan")
    repo_row = next(r for r in plan["repositories"] if r["id"] == repository)
    reader = Reader(artifact)
    overview = reader.overview()
    # The source revision for RGP/3.0 comes from the independent pinned
    # benchmark plan. Verify actual cited bytes against that Git revision.
    commit = repo_row["commit"]
    assert overview["source_commit"] in (None, commit), "RGP revision contradicts pinned Git"
    assert overview["snapshot_complete"], "a partial archive cannot ground the full task plan"

    source_cache: dict[str, bytes] = {}
    rows = []
    for task in expected:
        task_id = task["id"]
        terms = list(TERMS[task_id])
        result = context(reader, terms, max_files=4, max_bytes=24_000)
        assert not result["search_capped_terms"], f"{task_id}: lexical search was capped"
        assert not result["terms_without_source"], (
            f"{task_id}: query terms missing source passages: {result['terms_without_source']}")
        actual_paths = {s["path"] for s in result["sources"]}
        assert set(task["reference_paths"]) <= actual_paths, (
            f"{task_id}: missing reference paths: {set(task['reference_paths']) - actual_paths}")
        citations = []
        for source in result["sources"]:
            path = source["path"]
            if path not in source_cache:
                source_cache[path] = git_blob(source_repo, commit, path)
            original = source_cache[path]
            decoded = original.decode("utf-8")
            lines = decoded.splitlines(keepends=True)
            start, end, matched = source["start_line"], source["end_line"], source["match_line"]
            assert 1 <= start <= matched <= end <= len(lines), (
                f"{task_id}: invalid source location {path}:{start}-{end}")
            assert source["content"] == "".join(lines[start - 1:end]), (
                f"{task_id}: RGP excerpt differs from the pinned Git blob")
            assert source["match_term"] in lines[matched - 1], (
                f"{task_id}: source does not contain alleged search hit")
            citations.append({
                "path": path, "start_line": start, "end_line": end,
                "match_line": matched, "term": source["match_term"],
                "git_blob_sha1": hashlib.sha1(
                    b"blob " + str(len(original)).encode() + b"\0" + original
                ).hexdigest(),
            })
        rows.append({
            "task_id": task_id,
            "source_navigation": "PASS",
            "model_answer": "NOT_RUN_BY_CI",
            "answer_correctness": "NOT_INDEPENDENTLY_GRADED",
            "search_terms": terms,
            "cited_files": sorted(actual_paths),
            "source_passages": citations,
            "context_bytes": len(compact_json(result).encode("utf-8")),
            "selected_source_terms": sorted({s["match_term"] for s in result["sources"]}),
        })
    return {
        "kind": "rgp_twelve_frozen_agent_source_navigation",
        "status": "PASS",
        "repository": repository,
        "source_commit": commit,
        "source_snapshot_complete": True,
        "index_coverage": reader.coverage("index")["status"],
        "task_count": len(rows),
        "source_passages_compared_to_pinned_git": sum(len(x["source_passages"]) for x in rows),
        "unique_source_files_compared_to_pinned_git": len(source_cache),
        "github_equivalent_unique_source_bytes": sum(len(x) for x in source_cache.values()),
        "rgp_source_context_bytes": sum(x["context_bytes"] for x in rows),
        "tasks": rows,
        "model_inference": "NOT_RUN_BY_CI",
        "independent_answer_grading": "NOT_RUN",
        "provider_connector_calls": "NOT_MEASURED_BY_LOCAL_GIT",
        "gate_b": "HOLD",
        "method_limits": [
            "query terms were selected with knowledge of the frozen task references",
            "this is exact source navigation, not a blinded model answer evaluation",
            "Git show is a local source baseline, not a GitHub connector invocation",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", choices=("rgp", "serde_json", "itsdangerous"),
                        required=True)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args.artifact, args.source_repo, args.repository)
    except (AssertionError, RgpError, ValueError, OSError,
            subprocess.CalledProcessError) as exc:
        result = {
            "kind": "rgp_twelve_frozen_agent_source_navigation",
            "status": "FAIL", "repository": args.repository,
            "reason": str(exc), "gate_b": "HOLD",
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())

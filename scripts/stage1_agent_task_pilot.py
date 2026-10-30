#!/usr/bin/env python3
"""Three real, pinned repository questions through the agent context handoff.

This exercises the *production* trusted Python Reader and asks for
source-grounded passages for a developer question, then checks each
returned passage against git show of the frozen commit. It cannot
independently grade an LLM answer; no inference API is involved.
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
from rgp_reader import Reader, RgpError  # noqa: E402
from rgp_agent_context import context, compact_json  # noqa: E402

QUESTIONS = {
    "rgp": {
        "id": "rgp-search-cursor",
        "question": "Where is Reader search-cursor misuse rejected, and which test proves it?",
        "terms": ["_read_cursor", "test_search_keyset_cursor_scope_and_exhaustion"],
        "required_paths": ("python/rgp_reader.py", "tests/test_reader_api.py"),
    },
    "serde_json": {
        "id": "serde-json-ser-tests",
        "question": "Where are numeric and string JSON serialization implemented and tested?",
        "terms": ["fn serialize_i64", "fn serialize_f64", "fn serialize_str",
                  "fn test_write_list"],
        "required_paths": ("src/ser.rs", "tests/test.rs"),
    },
    "itsdangerous": {
        "id": "itsdangerous-timestamp",
        "question": "Where are signed timestamp expiry and future timestamp rejected?",
        "terms": ["age > max_age", "age < 0", "def test_max_age"],
        "required_paths": ("src/itsdangerous/timed.py",
                           "tests/test_itsdangerous/test_timed.py"),
    },
}


def run(artifact: Path, source_repo: Path, repository: str) -> dict:
    selected = QUESTIONS[repository]
    reader = Reader(artifact)
    source_commit = reader.overview()["source_commit"]
    assert reader.overview()["snapshot_complete"], "partial snapshot for agent task"
    result = context(reader, selected["terms"], max_files=4, max_bytes=24_000)
    observed_paths = {x["path"] for x in result["sources"]}
    for required in selected["required_paths"]:
        assert required in observed_paths, f"not enough source evidence: {required}"
    if repository == "serde_json":
        # A model needs all three scalar operations, not three i64
        # implementations selected because of lexical frequency.
        present = {s["match_term"] for s in result["sources"]
                   if s["path"] == "src/ser.rs"}
        assert {"fn serialize_i64", "fn serialize_f64", "fn serialize_str"} <= present, (
            "scalar serializer context is biased toward a repeated term")
    assert not result["search_capped_terms"], "source search was truncated"
    checked = []
    for source in result["sources"]:
        path = source["path"]
        original = subprocess.check_output(
            ["git", "-C", str(source_repo), "show", f"{source_commit}:{path}"])
        decoded = original.decode("utf-8")
        expected = "".join(decoded.splitlines(keepends=True)[
            source["start_line"] - 1:source["end_line"]])
        assert source["content"] == expected, f"source passage differs from Git: {path}"
        assert source["match_term"] in decoded.splitlines()[source["match_line"] - 1], (
            f"fabricated search hit: {path}:{source['match_line']}")
        checked.append({
            "path": path, "start_line": source["start_line"],
            "end_line": source["end_line"],
            "match_line": source["match_line"],
            "git_blob_sha1": hashlib.sha1(
                b"blob " + str(len(original)).encode() + b"\0" + original).hexdigest(),
        })
    assert checked, "agent received no source"
    return {
        "kind": "rgp_actual_agent_context_walkthrough",
        "status": "PASS",
        "repository": repository,
        "task_id": selected["id"],
        "question": selected["question"],
        "terms_chosen_in_advance": list(selected["terms"]),
        "source_commit": source_commit,
        "source_passages_compared_to_pinned_git": len(checked),
        "source_evidence": checked,
        "serialized_context_bytes": len(compact_json(result).encode("utf-8")),
        "source_snapshot_complete": result["snapshot_complete"],
        "index_coverage": result["index_coverage"],
        "model_answer": "NOT_EXECUTED_IN_THIS_TEST",
        "independent_answer_grading": "NOT_RUN",
        "github_baseline": "NOT_RUN_BY_THIS_SCRIPT",
        "gate_b": "HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", choices=tuple(QUESTIONS), required=True)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run(args.artifact, args.source_repo, args.repository)
    except (AssertionError, RgpError, ValueError, OSError,
            subprocess.CalledProcessError) as exc:
        report = {"kind": "rgp_actual_agent_context_walkthrough", "status": "FAIL",
                  "repository": args.repository, "reason": str(exc), "gate_b": "HOLD"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

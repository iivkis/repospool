#!/usr/bin/env python3
"""Stage 1: pinned three-repository real-RGP acceptance and resource profiling.

This is a stdlib-only developer-side test, NOT a model runner or Gate B.
The 12 frozen task ids / reference paths come from Gate B's existing plan;
the independently chosen anchors below are a test of Reader source navigation.
They are not full natural-language answers to the Gate B task rubrics.
No source code or RGP bytes are written to the evidence JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError  # noqa: E402
from gate_b import strict_json, validate_plan  # noqa: E402

# Fixed, repository-specific anchors selected before the CI experiments.
# Each plan reference path must be exercised, otherwise fail closed.
ANCHORS = {
    "rgp-transport-hash": {
        "python/rgp_reader.py": ("payload_sha256=",),
        "src/main.rs": ("payload_sha256=",),
    },
    "rgp-git-snapshot": {"src/main.rs": ("fn snapshot(",)},
    "rgp-search-cursor": {
        "python/rgp_reader.py": ("def _read_cursor(",),
        "tests/test_reader_api.py": ("def test_search_keyset_cursor_scope_and_exhaustion",),
    },
    "rgp-trust-boundary": {
        "python/rgp_bootstrap.py": ("def bootstrap(",),
        "docs/RGP_SECURITY_MODEL.md": ("Trust boundary",),
    },
    "serde-json-deserializer": {"src/de.rs": ("pub fn from_str(", "pub fn from_slice(")},
    "serde-json-serializer": {"src/ser.rs": ("fn serialize_i64(", "fn serialize_str(")},
    "serde-json-error-position": {
        "src/error.rs": ("pub fn line(", "pub fn column(", "pub fn classify("),
    },
    "serde-json-ser-tests": {
        "tests/test.rs": ("fn test_write_",),
        "src/ser.rs": ("fn serialize_str(",),
    },
    "itsdangerous-timestamp": {"src/itsdangerous/timed.py": ("class TimestampSigner", "def unsign(")},
    "itsdangerous-hmac": {"src/itsdangerous/signer.py": ("class HMACAlgorithm", "def get_signature(")},
    "itsdangerous-deserialization": {
        "src/itsdangerous/serializer.py": ("def load_payload(", "def loads("),
        "src/itsdangerous/signer.py": ("def get_signature(",),
    },
    "itsdangerous-expiry-tests": {
        "tests/test_itsdangerous/test_timed.py": ("def test_",),
        "src/itsdangerous/timed.py": ("class TimestampSigner",),
    },
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def tasks_for(plan: dict, repository: str) -> tuple[dict, list[dict]]:
    validate_plan(plan)
    repos = {r["id"]: r for r in plan["repositories"]}
    require(repository in repos, "unknown repository in pinned plan")
    require(set(ANCHORS) == {t["id"] for t in plan["tasks"]}, "anchor task ids diverge from frozen plan")
    for task in plan["tasks"]:
        anchor = ANCHORS[task["id"]]
        require(set(anchor) == set(task["reference_paths"]), f"anchor paths missing: {task['id']}")
        require(all(isinstance(v, tuple) and v and all(isinstance(s, str) and s for s in v)
                    for v in anchor.values()), "invalid source search anchor")
    return repos[repository], [t for t in plan["tasks"] if t["repository"] == repository]


def execute(command: list[str], *, cwd: Path | None = None) -> bytes:
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed with status {result.returncode}: "
                           f"{command[:4]!r}; {result.stderr[-1000:].decode(errors='replace')}")
    return result.stdout


def obtain_pinned_repo(repo: dict, dest: Path) -> None:
    # Only the immutable repo and SHA in the preregistered plan are used.
    url = f"https://github.com/{repo['github']}.git"
    execute(["git", "init", "-q", str(dest)])
    execute(["git", "-C", str(dest), "remote", "add", "origin", url])
    execute(["git", "-C", str(dest), "-c", "protocol.version=2", "fetch",
             "--depth=1", "--filter=blob:none", "origin", repo["commit"]])
    execute(["git", "-C", str(dest), "checkout", "--detach", "-q", "FETCH_HEAD"])
    actual = execute(["git", "-C", str(dest), "rev-parse", "HEAD"]).decode().strip()
    require(actual == repo["commit"], "pinned source revision mismatch")


def measured(command: list[str], metrics: Path) -> dict:
    # Ubuntu CI supplies GNU time. Resource measurements are per invocation;
    # no peak-memory extrapolation across hosts or machines is legitimate.
    execute(["/usr/bin/time", "-f", "%e %M", "-o", str(metrics), *command])
    pieces = metrics.read_text().strip().split()
    require(len(pieces) == 2, "GNU time did not produce two metrics")
    return {"elapsed_ms": round(float(pieces[0]) * 1000),
            "peak_rss_kib": int(pieces[1])}


def inspect(repo: dict, tasks: list[dict], source: Path, binary: Path, work: Path) -> dict:
    commit = repo["commit"]
    first, second = work / "a.rgp", work / "b.rgp"
    cmd = [str(binary), "generate", "--repo", str(source), "--rev", commit]
    first_metrics = measured([*cmd, "--output", str(first)], work / "time-a.txt")
    second_metrics = measured([*cmd, "--output", str(second)], work / "time-b.txt")
    raw = first.read_bytes()
    require(raw == second.read_bytes(), "RGP output nondeterministic at pinned commit")
    verify_metrics = measured([str(binary), "verify", "--artifact", str(first),
                               "--repo", str(source)], work / "time-verify.txt")

    start = time.perf_counter_ns()
    reader = Reader(first)
    validate_ms = round((time.perf_counter_ns() - start) / 1e6, 3)
    overview = reader.overview()
    require(reader.validate()["valid"] and overview["source_commit"] == commit,
            "Reader/commit validation failed")
    require(overview["snapshot_complete"], "fixed corpus RGP unexpectedly partial")
    require(reader.coverage("index")["status"] == "not_indexed", "semantic coverage overclaimed")

    paths = set()
    cursor = None
    while True:
        page = reader.tree(limit=17, cursor=cursor)
        require(not paths.intersection(page["paths"]), "duplicate tree paths across pages")
        paths.update(page["paths"])
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    require(len(paths) == overview["file_count"], "paginated Reader tree incomplete")
    seen_paths: dict[str, bytes] = {}
    task_rows = []
    query_count = 0
    task_start = time.perf_counter_ns()
    for task in tasks:
        rows = []
        for path in task["reference_paths"]:
            require(path in paths, f"task reference unavailable: {task['id']}:{path}")
            # Independent source-of-truth: raw git object from the pinned commit.
            original = seen_paths.get(path)
            if original is None:
                original = execute(["git", "-C", str(source), "show", f"{commit}:{path}"])
                seen_paths[path] = original
            # _blob is used for byte-fidelity verification only. All source
            # navigation tasks below use the PUBLIC read/search Reader API.
            require(reader._blob(path) == original, f"RGP content differs from Git: {path}")
            lines = original.decode("utf-8").splitlines(keepends=True)
            for needle in ANCHORS[task["id"]][path]:
                expected = [n for n, line in enumerate(lines, 1) if needle in line]
                require(0 < len(expected) < 100, f"anchor absent/ambiguous: {task['id']}:{path}")
                found = reader.search(needle, paths=[path], limit=100)
                observed = [match["line"] for match in found["matches"]]
                require(observed == expected and found["next_cursor"] is None,
                        f"search lines differ from pinned Git: {task['id']}:{path}")
                line_number = expected[0]
                from_rgp = reader.read(path, line_number, line_number)["content"]
                require(from_rgp == lines[line_number - 1],
                        f"Reader line content differs from Git: {task['id']}:{path}")
                query_count += 1
                rows.append({"path": path, "needle": needle,
                             "expected_lines": expected, "observed_lines": observed})
        task_rows.append({"task_id": task["id"], "status": "PASS", "anchors": rows})
    task_ms = round((time.perf_counter_ns() - task_start) / 1e6, 3)

    # Invalid bytes must still fail before returning any source content.
    for damaged, expected_error in (
        (raw[:-10], "TRUNCATED_ARTIFACT"),
        (raw.replace(b"RGP/1.0", b"RGP/2.0", 1), "UNSUPPORTED_FORMAT"),
        (re.sub(rb"(payload_sha256=)[0-9a-f]{64}", lambda m: m.group(1) + b"0" * 64,
                raw, count=1), "HASH_MISMATCH"),
    ):
        try:
            Reader(damaged)
        except RgpError as exc:
            require(exc.code == expected_error, "negative-case error changed")
        else:
            raise AssertionError("corrupted archive was accepted")
    return {
        "repository": repo["id"], "source_commit": commit, "status": "PASS",
        "rgp_sha256": hashlib.sha256(raw).hexdigest(), "rgp_bytes": len(raw),
        "included_files": overview["file_count"], "unique_reference_files": len(seen_paths),
        "query_count": query_count, "tasks": task_rows,
        "timings": {"generate_first": first_metrics, "generate_second": second_metrics,
                    "verify": verify_metrics, "reader_validate_ms": validate_ms,
                    "source_navigation_ms": task_ms},
        "reader_process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "checks": ["determinism", "Rust verification", "Reader completeness",
                   "all pinned source bytes", "source-based read/search",
                   "tree pagination", "invalid transport rejection"],
    }


def match_checkpoint(corpus: dict, result: dict) -> None:
    """Fail if generation silently changes the old pinned RGP/1.0 bytes."""
    rows = corpus.get("artifacts")
    require(isinstance(rows, list), "missing pinned checkpoint artifacts")
    checkpoints = [r for r in rows if r.get("repository") == result["repository"]]
    require(len(checkpoints) == 1, "missing or duplicate RGP checkpoint")
    expected = checkpoints[0]
    for key, current in (
        ("source_commit", result["source_commit"]),
        ("sha256", result["rgp_sha256"]),
        ("rgp_bytes", result["rgp_bytes"]),
        ("included_files", result["included_files"]),
    ):
        require(expected.get(key) == current, f"pinned compatibility regression: {key}")


def run(repository: str, binary: Path, plan_file: Path, checkpoint_file: Path) -> dict:
    plan = strict_json(plan_file)
    repo, tasks = tasks_for(plan, repository)
    from gate_b import digest_plan
    with tempfile.TemporaryDirectory(prefix="rgp-stage1-corpus-") as folder:
        work = Path(folder)
        source = work / "source"
        obtain_pinned_repo(repo, source)
        result = inspect(repo, tasks, source, binary.resolve(), work)
    match_checkpoint(strict_json(checkpoint_file), result)
    result["pinned_v1_format_compatibility"] = "PASS"
    result.update({
        "kind": "stage1_corpus_source_functional_and_resource_diagnostic",
        "plan_sha256": digest_plan(plan),
        "gate_b": "HOLD",
        "not_measured": ["GitHub connector calls", "model tokens", "agent answer grading",
                         "connector transport", "end-to-end acquisition latency"],
        "environment": "GitHub Actions ubuntu-latest, same runner per repository",
    })
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repository", choices=("rgp", "serde_json", "itsdangerous"), required=True)
    p.add_argument("--rust-cli", type=Path, default=ROOT / "target/release/rgp")
    p.add_argument("--plan", type=Path, default=ROOT / "benchmarks/gate_b/plan.json")
    p.add_argument("--checkpoint", type=Path, default=ROOT / "benchmarks/gate_b/corpus_preparation.json")
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    try:
        result = run(args.repository, args.rust_cli, args.plan, args.checkpoint)
    except Exception as exc:
        result = {"kind": "stage1_corpus_source_functional_and_resource_diagnostic",
                  "repository": args.repository, "status": "FAIL",
                  "reason": str(exc), "gate_b": "HOLD"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

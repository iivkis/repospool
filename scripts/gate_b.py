#!/usr/bin/env python3
"""Gate B: assistant-operated GitHub-vs-RGP observations, no model API or runner.

This validates recorded observations, not the truthfulness of self-reported
answers/call counts. That requires inspection of actual tool history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ARMS = ("github_connector", "rgp_reader")
HEX = frozenset("0123456789abcdef")


class ProtocolError(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ProtocolError(message)


def strict_json(path: Path) -> dict:
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, f"duplicate JSON field: {key}")
            out[key] = value
        return out

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    require(isinstance(value, dict), "expected JSON object")
    return value


def digest_plan(plan: dict) -> str:
    data = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def valid_commit(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(c in HEX for c in value)


def validate_plan(plan: dict) -> dict:
    require(plan.get("schema_version") == "2.0", "expected assistant-only plan v2.0")
    require(plan.get("method") == "assistant_in_chat", "an external model is not part of Gate B")
    require(plan.get("arms") == list(ARMS), "invalid comparison arms")
    limits = plan.get("requirements")
    require(isinstance(limits, dict) and limits.get("minimum_repositories") == 3, "need 3 repositories")
    require(type(limits.get("minimum_tasks")) is int and limits["minimum_tasks"] >= 10, "need >=10 tasks")
    require(limits.get("no_correctness_regressions") is True, "correctness guardrail missing")
    require(limits.get("minimum_connector_call_reduction") == 0.30, "call threshold changed")
    repos, tasks = plan.get("repositories"), plan.get("tasks")
    require(isinstance(repos, list) and len(repos) >= 3, "missing repositories")
    require(isinstance(tasks, list) and len(tasks) >= limits["minimum_tasks"], "missing tasks")
    by_id = {}
    github_repos = set()
    for repo in repos:
        require(isinstance(repo, dict) and isinstance(repo.get("id"), str)
                and repo["id"] and repo["id"] not in by_id, "invalid/duplicate repository")
        require(valid_commit(repo.get("commit")), "unpinned commit")
        github = repo.get("github")
        require(isinstance(github, str) and len(github.split("/")) == 2
                and all(part and part not in (".", "..") for part in github.split("/")),
                "invalid repo name")
        require(github not in github_repos, "duplicate GitHub repository")
        github_repos.add(github)
        by_id[repo["id"]] = repo
    ids = set()
    for task in tasks:
        require(isinstance(task, dict) and isinstance(task.get("id"), str)
                and task["id"] and task["id"] not in ids, "invalid/duplicate task")
        require(isinstance(task.get("repository"), str)
                and task["repository"] in by_id, "unknown task repository")
        require(isinstance(task.get("prompt"), str) and len(task["prompt"]) > 15, "invalid prompt")
        require(isinstance(task.get("rubric"), str) and len(task["rubric"]) > 15, "missing rubric")
        paths = task.get("reference_paths")
        require(isinstance(paths, list) and bool(paths) and all(isinstance(p, str) and p
                and not p.startswith("/") and all(seg not in ("", ".", "..") for seg in p.split("/"))
                for p in paths), "unsafe/missing source paths")
        require(len(paths) == len(set(paths)), "duplicate source paths")
        ids.add(task["id"])
    require({t["repository"] for t in tasks} == set(by_id), "every repository must have tasks")
    return {"plan_sha256": digest_plan(plan), "repositories": len(repos), "tasks": len(tasks),
            "expected_observations": len(tasks) * len(ARMS)}



def validate_github_cache(plan: dict, rows: dict, cache: object) -> dict:
    """Check first-use cache accounting against pinned paths and both arm's anchors.

    This is deterministic consistency checking. A JSON ledger is not a trusted
    recording of an actual connector call; independent chat-history review is
    still necessary before a GO decision.
    """
    require(isinstance(cache, dict) and cache.get("policy") == "first_use_pinned_blob",
            "unsupported GitHub source-cache policy")
    fetches = cache.get("source_fetches")
    require(isinstance(fetches, list), "source_fetches must be a list")
    repos = {repo["id"]: repo for repo in plan["repositories"]}
    expected = {}
    first_use_by_task = {task["id"]: 0 for task in plan["tasks"]}
    for task in plan["tasks"]:
        rid = task["repository"]
        for path in task["reference_paths"]:
            key = (rid, repos[rid]["commit"], path)
            if key not in expected:
                expected[key] = task["id"]
                first_use_by_task[task["id"]] += 1
    require(len(fetches) == len(expected), "cache ledger must contain exactly the unique required files")
    seen = set()
    blob_sha = {}
    for i, fetch in enumerate(fetches):
        require(isinstance(fetch, dict), f"cache fetch {i} must be an object")
        rid, commit, path, task_id = (fetch.get(k) for k in
                                       ("repository", "source_commit", "path", "task_id"))
        require(all(isinstance(v, str) for v in (rid, commit, path, task_id)),
                f"cache fetch {i}: invalid field type")
        key = (rid, commit, path)
        require(key in expected and key not in seen,
                f"cache fetch {i}: unknown or duplicate pinned source")
        require(task_id == expected[key],
                f"cache fetch {i}: source must be acquired at first task use")
        sha = fetch.get("git_blob_sha1")
        require(isinstance(sha, str) and len(sha) == 40 and all(c in HEX for c in sha),
                f"cache fetch {i}: invalid Git blob SHA-1")
        pinned_url = f'https://github.com/{repos[rid]["github"]}/blob/{commit}/{path}'
        require(fetch.get("source_url") == pinned_url,
                f"cache fetch {i}: source URL must pin repository, commit and path")
        seen.add(key)
        blob_sha[key] = sha
    require(seen == set(expected), "cache ledger has missing source files")
    for task in plan["tasks"]:
        tid, rid = task["id"], task["repository"]
        github_row = rows[(tid, ARMS[0])]
        require(github_row["connector_calls"] == first_use_by_task[tid],
                f"cache accounting mismatch for task {tid}")
        for arm in ARMS:
            row = rows[(tid, arm)]
            evidence = row.get("source_evidence")
            require(isinstance(evidence, dict) and set(evidence) == set(task["reference_paths"]),
                    f"cache task {tid}/{arm}: source evidence missing")
            for path in task["reference_paths"]:
                detail = evidence[path]
                key = (rid, repos[rid]["commit"], path)
                url = f'https://github.com/{repos[rid]["github"]}/blob/{key[1]}/{path}'
                require(isinstance(detail, dict) and detail.get("git_blob_sha1") == blob_sha[key]
                        and detail.get("source_url") == url,
                        f"cache task {tid}/{arm}: source anchor mismatch: {path}")
    return {"policy": "first_use_pinned_blob", "unique_source_fetches": len(fetches),
            "evidence_status": "self_reported_requires_independent_tool_history_review"}


def evaluate(plan: dict, measurements: dict) -> dict:
    info = validate_plan(plan)
    require(measurements.get("schema_version") == "2.0", "unsupported observations version")
    require(isinstance(measurements.get("observations"), list), "observations must be a list")
    rows = measurements["observations"]
    result = dict(gate="B", status="HOLD", plan_sha256=info["plan_sha256"],
                  expected_observations=info["expected_observations"], received_observations=len(rows),
                  reasons=[], call_reduction=None, token_reduction=None)
    if measurements.get("plan_sha256") != info["plan_sha256"]:
        result["reasons"].append("observation plan SHA mismatch")
    if measurements.get("method") != "assistant_in_chat":
        result["reasons"].append("only observations made by the assistant in this chat are eligible")
    tasks = {task["id"]: task for task in plan["tasks"]}
    repos = {repo["id"]: repo for repo in plan["repositories"]}
    seen = {}
    has_github_cache = "github_cache" in measurements
    for i, row in enumerate(rows):
        require(isinstance(row, dict), f"observation {i} must be an object")
        tid, arm = row.get("task_id"), row.get("arm")
        require(isinstance(tid, str) and tid in tasks
                and isinstance(arm, str) and arm in ARMS,
                f"observation {i}: invalid task/arm")
        key = (tid, arm)
        require(key not in seen, f"duplicate observation {key}")
        task = tasks[tid]
        require(row.get("repository") == task["repository"], f"observation {i}: wrong repository")
        require(row.get("source_commit") == repos[task["repository"]]["commit"], f"observation {i}: wrong commit")
        require(type(row.get("correct")) is bool, f"observation {i}: missing correctness assessment")
        require(type(row.get("connector_calls")) is int and row["connector_calls"] >= 0,
                f"observation {i}: connector_calls must be measured")
        require(arm != ARMS[0] or row["connector_calls"] >= 1 or has_github_cache,
                f"observation {i}: GitHub arm must include a source retrieval call")
        require(isinstance(row.get("answer"), str) and len(row["answer"].strip()) >= 15,
                f"observation {i}: answer missing")
        require(isinstance(row.get("evidence_url"), str) and row["evidence_url"].startswith("https://"),
                f"observation {i}: missing external evidence reference")
        source_paths = row.get("source_paths")
        require(isinstance(source_paths, list) and bool(source_paths)
                and all(isinstance(p, str) and p in task["reference_paths"] for p in source_paths)
                and len(source_paths) == len(task["reference_paths"])
                and set(source_paths) == set(task["reference_paths"]),
                f"observation {i}: source paths must cover each rubric reference exactly once")
        seen[key] = row
    if len(seen) != info["expected_observations"]:
        result["reasons"].append(f"{info['expected_observations'] - len(seen)} observations missing")
    preparation = measurements.get("rgp_preparation_calls")
    if not isinstance(preparation, dict) or set(preparation) != set(repos) or any(
            type(n) is not int or n < 1 for n in preparation.values()):
        result["reasons"].append("RGP artifact acquisition requires at least one counted connector call per repository")
    # No claimed savings from incomplete data or mismatched plan.
    if result["reasons"]:
        return result
    if has_github_cache:
        result["github_cache"] = validate_github_cache(plan, seen, measurements["github_cache"])
    baseline = sum(seen[(t, ARMS[0])]["connector_calls"] for t in tasks)
    rgp = sum(seen[(t, ARMS[1])]["connector_calls"] for t in tasks) + sum(preparation.values())
    result["github_calls"] = baseline
    result["rgp_calls_including_preparation"] = rgp
    result["call_reduction"] = None if baseline == 0 else 1 - rgp / baseline
    regressions = [tid for tid in tasks if seen[(tid, ARMS[0])]["correct"]
                   and not seen[(tid, ARMS[1])]["correct"]]
    result["correctness_regressions"] = regressions
    if regressions:
        result["reasons"].append("RGP correctness regressed")
    incorrect = [tid for tid in tasks if not seen[(tid, ARMS[1])]["correct"]]
    result["rgp_incorrect_tasks"] = incorrect
    if incorrect:
        result["reasons"].append("RGP answers did not meet all task rubrics")
    if result["call_reduction"] is None or result["call_reduction"] < 0.30:
        result["reasons"].append("connector-call reduction below 30%")
    # User-facing answers / tool logs still need inspection; a JSON file cannot attest itself.
    result["status"] = "PIVOT" if result["reasons"] else "GO_CANDIDATE"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "report"))
    parser.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    parser.add_argument("--observations", type=Path, default=Path("benchmarks/gate_b/observations.template.json"))
    args = parser.parse_args()
    try:
        plan = strict_json(args.plan)
        value = validate_plan(plan) if args.command == "plan" else evaluate(plan, strict_json(args.observations))
    except (ProtocolError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

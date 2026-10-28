#!/usr/bin/env python3
"""Gate B pre-registration validation and fail-closed scoring (stdlib only).

The evaluator does not invoke an agent. It only evaluates externally measured,
paired runs. Empty/incomplete/synthetic data can never pass the product gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

ARMS = ("github_connector", "rgp_actions_artifact")
HEX = frozenset("0123456789abcdef")


class ProtocolError(ValueError):
    pass


def strict_json(path: Path) -> dict:
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ProtocolError(f"duplicate JSON field: {k}")
            out[k] = v
        return out

    obj = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(obj, dict):
        raise ProtocolError("JSON document must be an object")
    return obj


def digest_plan(plan: dict) -> str:
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def valid_sha(sha: object) -> bool:
    return isinstance(sha, str) and len(sha) in (40, 64) and all(c in HEX for c in sha)


def validate_plan(plan: dict) -> dict:
    require(plan.get("schema_version") == "1.0", "unsupported plan version")
    require(plan.get("arms") == list(ARMS), "unrecognized comparison arms")
    minimum = plan.get("requirements", {})
    require(minimum.get("minimum_repositories") == 3, "benchmark must require 3 repositories")
    require(minimum.get("minimum_tasks") >= 10, "benchmark must require >=10 tasks")
    require(minimum.get("no_correctness_regressions") is True, "correctness guardrail missing")
    require(minimum.get("minimum_connector_call_reduction") == 0.30, "call threshold not preregistered")
    require(minimum.get("minimum_total_token_reduction") == 0.20, "token threshold not preregistered")
    repetitions = plan.get("repetitions_per_arm")
    require(type(repetitions) is int and repetitions >= 3, "minimum 3 repetitions per arm")
    repos = plan.get("repositories")
    tasks = plan.get("tasks")
    require(isinstance(repos, list) and len(repos) >= 3, "insufficient repositories")
    require(isinstance(tasks, list) and len(tasks) >= 10, "insufficient tasks")
    repo_ids = set()
    for repo in repos:
        require(isinstance(repo, dict) and isinstance(repo.get("id"), str), "invalid repo")
        require(repo["id"] not in repo_ids, "duplicate repository")
        require(valid_sha(repo.get("commit")), "repository commit must be pinned")
        require(isinstance(repo.get("github"), str) and repo["github"].count("/") == 1, "invalid GitHub repository")
        repo_ids.add(repo["id"])
    task_ids = set()
    for task in tasks:
        require(isinstance(task, dict), "invalid task")
        require(isinstance(task.get("id"), str) and task["id"] not in task_ids, "invalid/duplicate task id")
        require(task.get("repository") in repo_ids, "task references unknown repository")
        require(isinstance(task.get("prompt"), str) and len(task["prompt"]) > 15, "task missing prompt")
        require(isinstance(task.get("rubric"), str) and len(task["rubric"]) > 15, "task missing rubric")
        paths = task.get("reference_paths")
        require(isinstance(paths, list) and paths and all(isinstance(p, str) and not p.startswith("/") and ".." not in p.split("/") for p in paths), "missing/unsafe reference paths")
        task_ids.add(task["id"])
    require({t["repository"] for t in tasks} == repo_ids, "every pinned repo needs tasks")
    return {"plan_sha256": digest_plan(plan), "repositories": len(repos), "tasks": len(tasks), "repetitions_per_arm": repetitions}


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = math.ceil(p * len(s)) - 1
    return float(s[max(0, k)])


def describe(values: list[int]) -> dict:
    return {
        "median": statistics.median(values) if values else None,
        "p95": percentile(values, 0.95),
        "total": sum(values) if values else None,
    }


def evaluate(plan: dict, results: dict) -> dict:
    info = validate_plan(plan)
    trials = results.get("trials")
    require(results.get("schema_version") == "1.0", "unsupported result version")
    require(isinstance(trials, list), "trials must be an array")
    result = {
        "gate": "B", "plan_sha256": info["plan_sha256"],
        "status": "HOLD", "reasons": [],
        "repositories": info["repositories"], "tasks": info["tasks"],
        "expected_trials": info["tasks"] * info["repetitions_per_arm"] * 2,
        "received_trials": len(trials),
        "arms": {}, "thresholds": plan["requirements"],
    }
    if results.get("experiment_kind") != "real_agent":
        result["reasons"].append("Only real-agent trials are eligible; simulations are not evidence")
    if results.get("protocol_version") != "1.0":
        result["reasons"].append("Protocol version is missing or incompatible")
    if results.get("plan_sha256") != info["plan_sha256"]:
        result["reasons"].append("Trials are not bound to the preregistered plan SHA-256")
    model = results.get("model_id")
    if not isinstance(model, str) or not model.strip():
        result["reasons"].append("Model identity is missing")
    token_budget = results.get("token_budget")
    if type(token_budget) is not int or token_budget <= 0:
        result["reasons"].append("Fixed positive token budget is missing")
    if results.get("transport_verified") is not True:
        result["reasons"].append("Actual RGP connector transport has not been verified for these trials")

    repo_by_id = {r["id"]: r for r in plan["repositories"]}
    tasks_by_id = {t["id"]: t for t in plan["tasks"]}
    records = {}
    required_ints = ("connector_calls", "connector_transferred_bytes", "total_tokens",
                     "duration_ms", "preparation_ms", "preparation_bytes", "peak_memory_bytes",
                     "missed_dependencies", "false_positives")
    for i, trial in enumerate(trials):
        require(isinstance(trial, dict), f"trial {i} not an object")
        task_id, arm, rep = trial.get("task_id"), trial.get("arm"), trial.get("repetition")
        require(task_id in tasks_by_id and arm in ARMS, f"trial {i} invalid task or arm")
        require(type(rep) is int and 1 <= rep <= info["repetitions_per_arm"], f"trial {i} invalid repetition")
        key = (task_id, rep, arm)
        require(key not in records, f"duplicate trial {key}")
        require(trial.get("repository") == tasks_by_id[task_id]["repository"], f"trial {i} wrong repository")
        require(trial.get("source_commit") == repo_by_id[trial["repository"]]["commit"], f"trial {i} wrong source commit")
        require(trial.get("model_id") == model and trial.get("token_budget") == token_budget, f"trial {i} model or token budget mismatch")
        require(type(trial.get("correct")) is bool, f"trial {i} correctness missing")
        for field in required_ints:
            value = trial.get(field)
            require(type(value) is int and value >= 0, f"trial {i} missing or invalid {field}")
        require(trial["preparation_ms"] <= trial["duration_ms"], f"trial {i} preparation time exceeds duration")
        require(isinstance(trial.get("evidence_url"), str) and trial["evidence_url"].startswith("https://"), f"trial {i} evidence URL missing")
        require(isinstance(trial.get("errors"), list) and all(isinstance(e, str) for e in trial["errors"]), f"trial {i} errors list missing")
        records[key] = trial

    missing = [(tid, rep, arm) for tid in tasks_by_id for rep in range(1, info["repetitions_per_arm"] + 1)
               for arm in ARMS if (tid, rep, arm) not in records]
    if missing:
        result["reasons"].append(f"{len(missing)} paired trials missing (of {result['expected_trials']})")
    for arm in ARMS:
        arm_trials = [r for key, r in records.items() if key[2] == arm]
        result["arms"][arm] = {
            "n": len(arm_trials),
            "correct": sum(r["correct"] for r in arm_trials),
            "connector_calls": describe([r["connector_calls"] for r in arm_trials]),
            "connector_transferred_bytes": describe([r["connector_transferred_bytes"] for r in arm_trials]),
            "total_tokens": describe([r["total_tokens"] for r in arm_trials]),
            "duration_ms": describe([r["duration_ms"] for r in arm_trials]),
            "preparation_ms": describe([r["preparation_ms"] for r in arm_trials]),
            "preparation_bytes": describe([r["preparation_bytes"] for r in arm_trials]),
            "peak_memory_bytes": describe([r["peak_memory_bytes"] for r in arm_trials]),
            "missed_dependencies": sum(r["missed_dependencies"] for r in arm_trials),
            "false_positives": sum(r["false_positives"] for r in arm_trials),
            "errors": sum(len(r["errors"]) for r in arm_trials),
        }
    if result["reasons"]:
        return result
    base = result["arms"][ARMS[0]]
    rgp = result["arms"][ARMS[1]]
    regressions = [f"{task_id}@{rep}" for task_id in tasks_by_id
                   for rep in range(1, info["repetitions_per_arm"] + 1)
                   if records[(task_id, rep, ARMS[0])]["correct"]
                   and not records[(task_id, rep, ARMS[1])]["correct"]]
    call_baseline = base["connector_calls"]["median"]
    token_baseline = base["total_tokens"]["total"]
    calls_saved = None if call_baseline <= 0 else 1 - rgp["connector_calls"]["median"] / call_baseline
    tokens_saved = None if token_baseline <= 0 else 1 - rgp["total_tokens"]["total"] / token_baseline
    result["call_reduction"] = calls_saved
    result["token_reduction"] = tokens_saved
    result["correctness_regressions"] = regressions
    if regressions or rgp["correct"] < base["correct"]:
        result["reasons"].append("RGP correctness regressed versus matched baseline")
    if not ((calls_saved is not None and calls_saved >= 0.30)
            or (tokens_saved is not None and tokens_saved >= 0.20)):
        result["reasons"].append("Neither preregistered efficiency threshold was met")
    result["status"] = "PIVOT" if result["reasons"] else "GO"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "report"))
    parser.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    parser.add_argument("--trials", type=Path, default=Path("benchmarks/gate_b/trials.template.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = strict_json(args.plan)
        report = validate_plan(plan) if args.command == "plan" else evaluate(plan, strict_json(args.trials))
    except (ProtocolError, OSError, json.JSONDecodeError) as err:
        parser.error(str(err))
    serialized = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Gate B task-volume sensitivity, NOT additional observed benchmark trials.

Enumerate every nonempty subset of the frozen tasks using verified first-use
source anchors and an explicit conditional acquisition-cost model. No network,
model inference, or claim that the subsets were actually executed.
"""
from __future__ import annotations

import argparse
from itertools import combinations
import json
from pathlib import Path

from gate_b import ProtocolError, evaluate, require, strict_json, validate_plan

MODEL_VERSION = "1.0"
MAX_TASKS = 16


def validate_setup(plan: dict, observations: dict, setup: dict) -> tuple[int, dict]:
    require(isinstance(setup, dict) and setup.get("schema_version") == MODEL_VERSION
            and setup.get("kind") == "conditional_connector_call_sensitivity_not_new_measurement",
            "unsupported conditional setup model")
    require(setup.get("source_observations") ==
            "benchmarks/gate_b/observations.cache_fair_2026-10-29.json",
            "setup model does not identify the frozen cache-fair observations")
    components = setup.get("shared_preparation_calls")
    require(isinstance(components, dict) and set(components) ==
            {"corpus_checkpoint", "actions_artifact_listing", "pinned_reader_source"},
            "incomplete shared RGP preparation model")
    require(all(type(v) is int and v >= 1 for v in components.values()),
            "shared preparation components must be positive integers")
    downloads = setup.get("artifact_download_calls_per_repository")
    repo_ids = {r["id"] for r in plan["repositories"]}
    require(isinstance(downloads, dict) and set(downloads) == repo_ids,
            "missing repository acquisition costs")
    require(all(type(v) is int and v >= 1 for v in downloads.values()),
            "repository acquisition costs must be positive integers")
    require(isinstance(setup.get("assumptions"), list) and
            bool(setup["assumptions"]) and
            all(isinstance(t, str) and len(t) > 10 for t in setup["assumptions"]),
            "explicit assumptions required")
    shared = sum(components.values())
    measured = sum(observations["rgp_preparation_calls"].values())
    require(shared + sum(downloads.values()) == measured,
            "conditional cost components disagree with measured full-run acquisition")
    return shared, downloads


def task_subset_counts(tasks: tuple[dict, ...], shared: int, downloads: dict,
                       rgp_task_calls: dict) -> tuple[int, int]:
    require(bool(tasks), "empty task subset has no acquisition scenario")
    unique = {(t["repository"], path) for t in tasks for path in t["reference_paths"]}
    involved = {t["repository"] for t in tasks}
    github = len(unique)
    rgp = shared + sum(downloads[r] for r in involved) + sum(
        rgp_task_calls[t["id"]] for t in tasks)
    return github, rgp


def analyze(plan: dict, observations: dict, setup: dict) -> dict:
    info = validate_plan(plan)
    report = evaluate(plan, observations)
    tasks = tuple(plan["tasks"])
    require(0 < len(tasks) <= MAX_TASKS, "task count outside safe exhaustive enumeration limit")
    require("github_cache" in report and report["github_cache"]["policy"] ==
            "first_use_pinned_blob" and
            report["received_observations"] == info["expected_observations"],
            "source-cache evidence or complete paired observations missing")
    require("github_calls" in report and "rgp_calls_including_preparation" in report,
            "measured full-run accounting incomplete")
    shared, downloads = validate_setup(plan, observations, setup)
    rows = {(o["task_id"], o["arm"]): o for o in observations["observations"]}
    rgp_task_calls = {t["id"]: rows[t["id"], "rgp_reader"]["connector_calls"] for t in tasks}
    full_github, full_rgp = task_subset_counts(tasks, shared, downloads, rgp_task_calls)
    require((full_github, full_rgp) ==
            (report["github_calls"], report["rgp_calls_including_preparation"]),
            "scenario model does not reproduce the measured full run")
    require(full_github == report["github_cache"]["unique_source_fetches"],
            "full-run GitHub source count disagrees with first-use ledger")

    sizes = []
    for n in range(1, len(tasks) + 1):
        row = {"tasks": n, "subsets": 0, "rgp_fewer_calls": 0,
               "github_fewer_calls": 0, "same_calls": 0, "at_least_30_percent": 0,
               "min_reduction_pct": None, "max_reduction_pct": None}
        for selected in combinations(tasks, n):
            github, rgp = task_subset_counts(selected, shared, downloads, rgp_task_calls)
            # Compare exact integers, avoiding rounding a borderline scenario into a win.
            diff = github - rgp
            reduction_pct = 100 * diff / github
            row["subsets"] += 1
            row["rgp_fewer_calls"] += int(diff > 0)
            row["github_fewer_calls"] += int(diff < 0)
            row["same_calls"] += int(diff == 0)
            row["at_least_30_percent"] += int(10 * diff >= 3 * github)
            lo, hi = row["min_reduction_pct"], row["max_reduction_pct"]
            row["min_reduction_pct"] = reduction_pct if lo is None else min(lo, reduction_pct)
            row["max_reduction_pct"] = reduction_pct if hi is None else max(hi, reduction_pct)
        sizes.append(row)

    return {
        "kind": "conditional_exhaustive_task_subset_sensitivity",
        "decision": "HOLD_pending_independent_correctness_and_tool_history_review",
        "new_experimental_trials": 0,
        "plan_sha256": info["plan_sha256"],
        "source_observation_status": observations.get("session_metadata", {}).get("status"),
        "full_run_measured_calls": {"github": full_github, "rgp": full_rgp},
        "full_run_reduction_pct": 100 * (full_github - full_rgp) / full_github,
        "conditional_shared_setup_calls": shared,
        "conditional_download_calls_per_repository": downloads,
        "assumptions": setup["assumptions"],
        "total_nonempty_subsets": sum(row["subsets"] for row in sizes),
        "by_task_count": sizes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    parser.add_argument("--observations", type=Path, default=Path(
        "benchmarks/gate_b/observations.cache_fair_2026-10-29.json"))
    parser.add_argument("--model", type=Path, default=Path(
        "benchmarks/gate_b/setup_cost_model.json"))
    args = parser.parse_args()
    try:
        result = analyze(strict_json(args.plan), strict_json(args.observations),
                         strict_json(args.model))
    except (ProtocolError, OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

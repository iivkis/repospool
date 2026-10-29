#!/usr/bin/env python3
"""Gate B: source-backed, arm-masked independent review handoff (stdlib only).

This checks the *structure* of a reviewer verdict; neither the reviewer identity
nor actual tool-call history can be authenticated by a JSON file. No model run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets

from gate_b import ARMS, HEX, ProtocolError, digest_plan, evaluate, require, strict_json, validate_plan

PACKET_VERSION = "1.0"


def hash_answer(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def validate_observations(plan: dict, observations: dict) -> tuple[dict, dict]:
    info = validate_plan(plan)
    report = evaluate(plan, observations)
    require(observations.get("plan_sha256") == info["plan_sha256"],
            "observations do not match pinned plan")
    require(observations.get("method") == "assistant_in_chat", "wrong observation method")
    require(report["received_observations"] == info["expected_observations"],
            "incomplete paired observations")
    require("github_calls" in report and "rgp_calls_including_preparation" in report,
            "incomplete observation accounting")
    rows = {(r["task_id"], r["arm"]): r for r in observations["observations"]}
    return rows, report


def source_refs(task: dict, repo: dict) -> list[dict]:
    return [{"path": path,
             "url": f'https://github.com/{repo["github"]}/blob/{repo["commit"]}/{path}'}
            for path in task["reference_paths"]]


def make_packet(plan: dict, observations: dict) -> dict:
    rows, _ = validate_observations(plan, observations)
    repos = {repo["id"]: repo for repo in plan["repositories"]}
    tasks = []
    for task in plan["tasks"]:
        order = list(ARMS)
        if secrets.randbelow(2):
            order.reverse()
        answers = []
        for label, arm in zip(("A", "B"), order):
            answer = rows[(task["id"], arm)]["answer"]
            answers.append({"case_id": label, "answer": answer,
                            "answer_sha256": hash_answer(answer)})
        tasks.append({"task_id": task["id"], "repository": task["repository"],
                      "source_commit": repos[task["repository"]]["commit"],
                      "prompt": task["prompt"], "rubric": task["rubric"],
                      "source_refs": source_refs(task, repos[task["repository"]]),
                      "answers": answers})
    return {"schema_version": PACKET_VERSION, "kind": "arm_masked_review_packet",
            "plan_sha256": digest_plan(plan),
            "observations_sha256": digest_plan(observations),
            "nonce": secrets.token_hex(16), "tasks": tasks}


def validate_packet(plan: dict, observations: dict, packet: dict) -> None:
    rows, _ = validate_observations(plan, observations)
    require(isinstance(packet, dict) and packet.get("schema_version") == PACKET_VERSION
            and packet.get("kind") == "arm_masked_review_packet",
            "unsupported review packet")
    require(set(packet) == {"schema_version", "kind", "plan_sha256",
                            "observations_sha256", "nonce", "tasks"},
            "unexpected or missing review packet fields")
    require(packet["plan_sha256"] == digest_plan(plan)
            and packet["observations_sha256"] == digest_plan(observations),
            "review packet provenance mismatch")
    nonce = packet["nonce"]
    require(isinstance(nonce, str) and len(nonce) == 32 and all(c in HEX for c in nonce),
            "invalid packet nonce")
    repos = {repo["id"]: repo for repo in plan["repositories"]}
    items = packet["tasks"]
    require(isinstance(items, list) and len(items) == len(plan["tasks"]),
            "review packet task count mismatch")
    for task, item in zip(plan["tasks"], items):
        require(isinstance(item, dict) and set(item) ==
                {"task_id", "repository", "source_commit", "prompt",
                 "rubric", "source_refs", "answers"}, "malformed review task")
        repo = repos[task["repository"]]
        for field, expected in (
            ("task_id", task["id"]), ("repository", task["repository"]),
            ("source_commit", repo["commit"]), ("prompt", task["prompt"]),
            ("rubric", task["rubric"]), ("source_refs", source_refs(task, repo))
        ):
            require(item[field] == expected, f"review task {task['id']}: altered {field}")
        answers = item["answers"]
        require(isinstance(answers, list) and len(answers) == 2,
                f"review task {task['id']}: missing answers")
        require(all(isinstance(a, dict) and set(a) == {"case_id", "answer", "answer_sha256"}
                    for a in answers), f"review task {task['id']}: malformed answer")
        require([a["case_id"] for a in answers] == ["A", "B"],
                f"review task {task['id']}: invalid case identifiers")
        require(all(isinstance(a["answer"], str)
                    and a["answer_sha256"] == hash_answer(a["answer"])
                    for a in answers), f"review task {task['id']}: answer digest mismatch")
        original = Counter(rows[(task["id"], arm)]["answer"] for arm in ARMS)
        received = Counter(a["answer"] for a in answers)
        require(original == received, f"review task {task['id']}: answer substitution")


def validate_source_line_counts(plan: dict, observations: dict, source_lines: dict,
                                checkpoint: dict | None = None) -> dict:
    """Validate a pinned source line-count index against the planned Git blobs.

    Manifest metadata does not alone prove the underlying source bytes. The
    generator separately checks those bytes against independently pinned
    RGP SHA-256 and Git blob SHA-1 values.
    """
    rows, _ = validate_observations(plan, observations)
    require(isinstance(source_lines, dict) and
            source_lines.get("schema_version") == "1.0" and
            source_lines.get("method") == "verified_pinned_rgp_reader_plus_git_blob_sha1" and
            source_lines.get("plan_sha256") == digest_plan(plan),
            "invalid source-line index")
    sha_map = source_lines.get("source_rgp_sha256")
    require(isinstance(sha_map, dict), "missing source RGP SHA-256 anchors")
    repos = {repo["id"]: repo for repo in plan["repositories"]}
    require(set(sha_map) == set(repos) and
            all(isinstance(s, str) and len(s) == 64 and all(c in HEX for c in s)
                for s in sha_map.values()), "invalid source RGP SHA-256 anchors")
    if checkpoint is not None:
        require(isinstance(checkpoint, dict) and isinstance(checkpoint.get("artifacts"), list),
                "invalid corpus checkpoint")
        expected_shas = {
            r["repository"]: r["sha256"] for r in checkpoint["artifacts"]
            if isinstance(r, dict) and isinstance(r.get("repository"), str)
        }
        require(sha_map == expected_shas, "source-line RGP SHA-256 checkpoint mismatch")
    files = source_lines.get("files")
    require(isinstance(files, list), "source-line files must be a list")
    expected = {}
    for task in plan["tasks"]:
        tid, rid = task["id"], task["repository"]
        row = rows[(tid, ARMS[0])]
        evidence = row.get("source_evidence")
        require(isinstance(evidence, dict), f"task {tid}: source anchors missing")
        for path in task["reference_paths"]:
            require(path in evidence and isinstance(evidence[path], dict) and
                    isinstance(evidence[path].get("git_blob_sha1"), str),
                    f"task {tid}: source anchor missing for {path}")
            key = (rid, path)
            sha = evidence[path]["git_blob_sha1"]
            require(key not in expected or expected[key] == sha,
                    f"task {tid}: conflicting pinned blob SHA-1")
            expected[key] = sha
    require(len(files) == len(expected), "source-line index incomplete or has extra entries")
    seen = {}
    for i, entry in enumerate(files):
        require(isinstance(entry, dict) and set(entry) ==
                {"repository", "source_commit", "path", "git_blob_sha1", "line_count"},
                f"source-line index entry {i}: malformed")
        rid, path = entry["repository"], entry["path"]
        require(isinstance(rid, str) and isinstance(path, str),
                f"source-line index entry {i}: invalid repository/path")
        key = (rid, path)
        require(key in expected and key not in seen, "unknown/duplicate source-line entry")
        require(entry["source_commit"] == repos[rid]["commit"] and
                entry["git_blob_sha1"] == expected[key],
                f"source-line index {rid}/{path}: wrong pinned source")
        count = entry["line_count"]
        require(type(count) is int and 1 <= count <= 10_000_000,
                f"source-line index {rid}/{path}: invalid line count")
        seen[key] = count
    require(set(seen) == set(expected), "source-line index missing source")
    return seen


def blank_review(packet: dict) -> dict:
    return {"schema_version": PACKET_VERSION,
            "packet_sha256": digest_plan(packet),
            "reviewer": {"id": "", "independent_of_observation_author": False,
                         "review_record_url": ""},
            "judgments": [
                {"task_id": item["task_id"], "case_id": answer["case_id"],
                 "correct": None, "evidence": [], "rationale": ""}
                for item in packet["tasks"] for answer in item["answers"]
            ]}


def review_report(plan: dict, observations: dict, packet: dict, review: dict,
                  source_lines: dict | None = None,
                  checkpoint: dict | None = None) -> dict:
    validate_packet(plan, observations, packet)
    rows, accounting = validate_observations(plan, observations)
    line_bounds = (validate_source_line_counts(plan, observations, source_lines, checkpoint)
                   if source_lines is not None else None)
    require(isinstance(review, dict) and review.get("schema_version") == PACKET_VERSION,
            "unsupported review")
    require(review.get("packet_sha256") == digest_plan(packet),
            "review is not bound to this exact masked packet")
    judgments = review.get("judgments")
    require(isinstance(judgments, list), "judgments must be a list")
    expected = {(item["task_id"], a["case_id"])
                for item in packet["tasks"] for a in item["answers"]}
    tasks = {item["task_id"]: item for item in packet["tasks"]}
    seen = {}
    incomplete = []
    for i, row in enumerate(judgments):
        require(isinstance(row, dict), f"judgment {i}: not an object")
        tid, case = row.get("task_id"), row.get("case_id")
        require(isinstance(tid, str) and isinstance(case, str)
                and (tid, case) in expected and (tid, case) not in seen,
                f"judgment {i}: unknown or duplicate case")
        require(type(row.get("correct")) is bool or row.get("correct") is None,
                f"judgment {i}: correct must be boolean or null")
        evidence = row.get("evidence")
        require(isinstance(evidence, list), f"judgment {i}: evidence must be a list")
        rationale = row.get("rationale")
        require(isinstance(rationale, str), f"judgment {i}: rationale must be text")
        if row["correct"] is None:
            incomplete.append((tid, case))
        else:
            allowed = {x["path"] for x in tasks[tid]["source_refs"]}
            paths = set()
            for entry in evidence:
                require(isinstance(entry, dict), f"judgment {i}: evidence entry not object")
                path, start, end = (entry.get(k) for k in ("path", "start_line", "end_line"))
                require(isinstance(path, str) and path in allowed
                        and type(start) is int and type(end) is int
                        and 1 <= start <= end, f"judgment {i}: invalid source line evidence")
                if line_bounds is not None:
                    rid = tasks[tid]["repository"]
                    require(end <= line_bounds[(rid, path)],
                            f"judgment {i}: source line evidence out of bounds for {path}")
                paths.add(path)
            require(paths == allowed, f"judgment {i}: not all required source paths cited")
            require(len(rationale.strip()) >= 15,
                    f"judgment {i}: missing substantive rationale")
        seen[(tid, case)] = row
    incomplete.extend(sorted(expected - set(seen)))
    reviewer = review.get("reviewer")
    require(isinstance(reviewer, dict), "reviewer must be an object")
    reviewer_id = reviewer.get("id")
    independent = reviewer.get("independent_of_observation_author") is True
    url = reviewer.get("review_record_url")
    identified = (isinstance(reviewer_id, str) and bool(reviewer_id.strip())
                  and reviewer_id.strip().lower() not in ("assistant", "assistant-in-chat")
                  and isinstance(url, str) and url.startswith("https://"))
    summary = {"gate": "B", "status": "HOLD", "reviewed": len(expected) - len(incomplete),
               "expected": len(expected), "missing": len(incomplete),
               "reviewer_independence": "self_declared_unverified",
               "connector_calls": {"github": accounting["github_calls"],
                                   "rgp": accounting["rgp_calls_including_preparation"]},
               "call_reduction": accounting["call_reduction"],
               "reasons": []}
    if incomplete:
        summary["reasons"].append("independent verdicts incomplete")
    if not identified or not independent:
        summary["reasons"].append("independent reviewer declaration or review record missing")
    if line_bounds is None:
        summary["reasons"].append("verified source-line index required before candidate review")
    if summary["reasons"]:
        return summary

    graded = {}
    for item in packet["tasks"]:
        tid = item["task_id"]
        # Match the exact answer text, not a claimed arm label in reviewer input.
        for arm in ARMS:
            answer = rows[(tid, arm)]["answer"]
            cases = [a["case_id"] for a in item["answers"] if a["answer"] == answer]
            verdicts = {seen[(tid, case)]["correct"] for case in cases}
            require(len(verdicts) == 1, f"review task {tid}: same answer received conflicting grades")
            graded[(tid, arm)] = verdicts.pop()
    incorrect = [t["id"] for t in plan["tasks"] if not graded[(t["id"], ARMS[1])]]
    regressions = [t["id"] for t in plan["tasks"] if graded[(t["id"], ARMS[0])]
                   and not graded[(t["id"], ARMS[1])]]
    summary["rgp_incorrect_tasks"] = incorrect
    summary["correctness_regressions"] = regressions
    if incorrect:
        summary["reasons"].append("independent review found incorrect RGP answers")
    if regressions:
        summary["reasons"].append("independent review found RGP correctness regressions")
    if accounting["call_reduction"] is None or accounting["call_reduction"] < 0.30:
        summary["reasons"].append("connector-call reduction below 30%")
    summary["status"] = "PIVOT" if summary["reasons"] else "REVIEW_CANDIDATE"
    if summary["status"] == "REVIEW_CANDIDATE":
        summary["reasons"].append("independence and connector history still require external verification")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pack", "template", "check"))
    parser.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    parser.add_argument("--observations", type=Path, default=Path(
        "benchmarks/gate_b/observations.cache_fair_2026-10-29.json"))
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--source-lines", type=Path,
                        default=Path("benchmarks/gate_b/source_line_counts.json"))
    parser.add_argument("--corpus-checkpoint", type=Path,
                        default=Path("benchmarks/gate_b/corpus_preparation.json"))
    args = parser.parse_args()
    try:
        plan, observations = strict_json(args.plan), strict_json(args.observations)
        if args.command == "pack":
            result = make_packet(plan, observations)
        else:
            require(args.packet is not None, "--packet required")
            packet = strict_json(args.packet)
            validate_packet(plan, observations, packet)
            if args.command == "template":
                result = blank_review(packet)
            else:
                require(args.review is not None, "--review required")
                result = review_report(plan, observations, packet, strict_json(args.review),
                                       strict_json(args.source_lines),
                                       strict_json(args.corpus_checkpoint))
    except (ProtocolError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

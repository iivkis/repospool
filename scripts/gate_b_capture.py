#!/usr/bin/env python3
"""Audit real Gate B agent traces and compile externally measured paired trials.

Never runs agents, estimates tokens or fabricates missing measurements.
Session files are untrusted records; provenance must be checked independently.
Python stdlib only, for development/benchmark infrastructure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from gate_b import ARMS, ProtocolError, digest_plan, evaluate, strict_json, validate_plan

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_SESSION_BYTES = 2 * 1024 * 1024


def required(obj: dict, field: str, kind: type):
    value = obj.get(field)
    if type(value) is not kind:
        raise ProtocolError(f"{field}: expected {kind.__name__}")
    return value


def nonnegative(obj: dict, field: str) -> int:
    value = required(obj, field, int)
    if value < 0:
        raise ProtocolError(f"{field}: must be nonnegative")
    return value


def nonempty(obj: dict, field: str) -> str:
    value = required(obj, field, str)
    if not value.strip():
        raise ProtocolError(f"{field}: must be nonempty")
    return value


def parse_session(path: Path) -> dict:
    if path.stat().st_size > MAX_SESSION_BYTES:
        raise ProtocolError(f"session too large: {path}")
    return strict_json(path)


def to_trial(plan: dict, session: dict, *, trace_digest: str) -> dict:
    """Derive every numerical trial field from a typed event in the trace."""
    validate_plan(plan)
    if session.get("schema_version") != "1.0" or session.get("experiment_kind") != "real_agent":
        raise ProtocolError("session must be v1.0 real_agent, not simulation")
    if session.get("plan_sha256") != digest_plan(plan):
        raise ProtocolError("session plan SHA-256 mismatch")
    tasks = {t["id"]: t for t in plan["tasks"]}
    repos = {r["id"]: r for r in plan["repositories"]}
    task_id = nonempty(session, "task_id")
    if task_id not in tasks:
        raise ProtocolError("task not preregistered")
    arm = nonempty(session, "arm")
    if arm not in ARMS:
        raise ProtocolError("unknown arm")
    rep = required(session, "repetition", int)
    if not 1 <= rep <= plan["repetitions_per_arm"]:
        raise ProtocolError("repetition out of range")
    repository = tasks[task_id]["repository"]
    if session.get("repository") != repository or session.get("source_commit") != repos[repository]["commit"]:
        raise ProtocolError("source/repository mismatch")
    model = nonempty(session, "model_id")
    budget = nonnegative(session, "token_budget")
    if budget == 0:
        raise ProtocolError("token budget cannot be zero")
    uri = nonempty(session, "evidence_url")
    if not uri.startswith("https://"):
        raise ProtocolError("evidence_url must be HTTPS")
    if not re.fullmatch(r"[0-9a-f]{64}", trace_digest):
        raise ProtocolError("invalid trace digest")
    events = session.get("events")
    if not isinstance(events, list) or not events:
        raise ProtocolError("events must be nonempty")
    calls = 0
    transferred = 0
    tokens = 0
    count_usage = 0
    count_prep = 0
    count_summary = 0
    count_grade = 0
    preparation_ms = 0
    preparation_bytes = 0
    duration_ms = 0
    peak_memory_bytes = 0
    errors = []
    grade = None
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ProtocolError(f"event {index} must be an object")
        typ = event.get("type")
        if typ == "connector_call":
            nonempty(event, "operation")
            nonempty(event, "provider")
            nonnegative(event, "duration_ms")
            transferred += nonnegative(event, "transferred_bytes")
            required(event, "success", bool)
            calls += 1
            if not event["success"]:
                errors.append(nonempty(event, "error"))
        elif typ == "model_usage":
            if event.get("source") != "provider_reported":
                raise ProtocolError("token counts must come from provider-reported usage")
            tokens += nonnegative(event, "input_tokens") + nonnegative(event, "output_tokens")
            count_usage += 1
        elif typ == "preparation":
            count_prep += 1
            preparation_ms = nonnegative(event, "duration_ms")
            preparation_bytes = nonnegative(event, "transferred_or_generated_bytes")
        elif typ == "run_summary":
            count_summary += 1
            duration_ms = nonnegative(event, "duration_ms")
            peak_memory_bytes = nonnegative(event, "peak_memory_bytes")
            raw_errors = event.get("errors")
            if not isinstance(raw_errors, list) or not all(isinstance(e, str) for e in raw_errors):
                raise ProtocolError("run_summary errors must be a list of strings")
            errors.extend(raw_errors)
        elif typ == "assessment":
            count_grade += 1
            required(event, "correct", bool)
            nonempty(event, "assessor")
            nonempty(event, "rationale")
            nonempty(event, "answer_sha256")
            if not HEX64.fullmatch(event["answer_sha256"]):
                raise ProtocolError("assessment requires a SHA-256 of the actual agent answer")
            nonnegative(event, "missed_dependencies")
            nonnegative(event, "false_positives")
            grade = event
        else:
            raise ProtocolError(f"event {index}: unknown type {typ!r}")
    if (count_usage < 1 or tokens == 0 or count_prep != 1 or count_summary != 1 or count_grade != 1):
        raise ProtocolError("each session needs provider usage, one preparation, summary and assessment")
    if duration_ms <= 0 or preparation_ms > duration_ms:
        raise ProtocolError("invalid measured duration/preparation")
    transported = session.get("transport_verified")
    if type(transported) is not bool:
        raise ProtocolError("transport_verified must be explicit")
    if arm == "rgp_actions_artifact":
        hash_value = session.get("trusted_artifact_sha256")
        if transported and (not isinstance(hash_value, str) or not HEX64.fullmatch(hash_value)):
            raise ProtocolError("RGP delivery requires independently verified artifact hash")
    return {
        "task_id": task_id, "repository": repository,
        "source_commit": repos[repository]["commit"],
        "repetition": rep, "arm": arm, "model_id": model,
        "token_budget": budget, "correct": grade["correct"],
        "connector_calls": calls, "connector_transferred_bytes": transferred,
        "total_tokens": tokens, "duration_ms": duration_ms,
        "preparation_ms": preparation_ms, "preparation_bytes": preparation_bytes,
        "peak_memory_bytes": peak_memory_bytes,
        "missed_dependencies": grade["missed_dependencies"],
        "false_positives": grade["false_positives"],
        "errors": errors, "evidence_url": uri,
        "trace_sha256": trace_digest,
    }


def compile_sessions(plan: dict, session_paths: list[Path]) -> dict:
    validate_plan(plan)
    rows = []
    models = set()
    budgets = set()
    hashes = {}
    delivered = True
    for path in sorted(session_paths):
        if path.stat().st_size > MAX_SESSION_BYTES:
            raise ProtocolError(f"session too large: {path}")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        session = parse_session(path)
        trial = to_trial(plan, session, trace_digest=digest)
        rows.append(trial)
        models.add(trial["model_id"])
        budgets.add(trial["token_budget"])
        hashes[path.name] = digest
        if trial["arm"] == "rgp_actions_artifact" and session["transport_verified"] is not True:
            delivered = False
    if len(models) > 1 or len(budgets) > 1:
        raise ProtocolError("sessions use different model identities or token budgets")
    return {
        "schema_version": "1.0", "experiment_kind": "real_agent",
        "protocol_version": "1.0", "plan_sha256": digest_plan(plan),
        "model_id": next(iter(models), None),
        "token_budget": next(iter(budgets), None),
        "transport_verified": bool(rows) and delivered and any(r["arm"] == "rgp_actions_artifact" for r in rows),
        "trials": rows, "trace_sha256_by_filename": hashes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-session", "collect"))
    parser.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    parser.add_argument("--session", type=Path)
    parser.add_argument("--sessions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = strict_json(args.plan)
        if args.command == "check-session":
            if args.session is None:
                raise ProtocolError("--session is required")
            if args.session.stat().st_size > MAX_SESSION_BYTES:
                raise ProtocolError(f"session too large: {args.session}")
            raw = args.session.read_bytes()
            report = to_trial(plan, parse_session(args.session), trace_digest=hashlib.sha256(raw).hexdigest())
        else:
            if args.sessions is None or not args.sessions.is_dir():
                raise ProtocolError("--sessions must be a directory")
            report = compile_sessions(plan, list(args.sessions.glob("*.json")))
            scoring = evaluate(plan, report)
            print(json.dumps(scoring, indent=2, sort_keys=True))
        serialized = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        else:
            print(serialized, end="")
    except (ProtocolError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

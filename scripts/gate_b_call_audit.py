#!/usr/bin/env python3
"""Gate B: reconcile a provider-call transcript with the frozen comparison.

The original cache-fair report records 13 unique source fetches versus six
RGP setup/acquisition calls. Those are *claims*, not an authenticated tool
transcript. A blank receipt ledger is generated without inventing calls.
A completely filled ledger can only become TRACE_CANDIDATE, never GO;
the source provider's original logs still need independent authentication.

No model runner or third-party Python dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from gate_b import ProtocolError, digest_plan, require, strict_json, validate_plan
from gate_b_review import validate_observations

VERSION = "1.0"
HEX = frozenset("0123456789abcdef")
GITHUB_SOURCE_TOOLS = frozenset({
    "mcp__GitHub__fetch_file", "mcp__GitHub__fetch", "mcp__GitHub__fetch_blob",
})
RGP_ACQUISITION_TOOLS = frozenset({
    "mcp__GitHub__fetch", "mcp__GitHub__fetch_workflow_run_artifacts",
    "mcp__GitHub__download_workflow_artifact", "mcp__GitHub__fetch_workflow_run_jobs",
})


def make_template(plan: dict, observations: dict) -> dict:
    info = validate_plan(plan)
    rows, accounting = validate_observations(plan, observations)
    cache = observations.get("github_cache")
    require(isinstance(cache, dict), "missing first-use GitHub source accounting")
    fetches = cache.get("source_fetches")
    require(isinstance(fetches, list), "missing pinned GitHub source fetches")
    entries = []
    for entry in fetches:
        require(isinstance(entry, dict), "invalid cached source entry")
        entries.append({
            "task_id": entry["task_id"], "repository": entry["repository"],
            "source_commit": entry["source_commit"], "path": entry["path"],
            "source_url": entry["source_url"],
            "git_blob_sha1": entry["git_blob_sha1"], "receipt": None,
        })
    setup = observations.get("rgp_preparation_calls")
    require(isinstance(setup, dict), "missing RGP preparation accounting")
    rgp = []
    for repo in plan["repositories"]:
        name = repo["id"]
        for ordinal in range(setup[name]):
            rgp.append({"repository": name, "source_commit": repo["commit"],
                        "slot": ordinal + 1, "receipt": None})
    require(len(entries) == accounting["github_calls"] and
            len(rgp) == accounting["rgp_calls_including_preparation"],
            "claimed counts disagree with expected source and setup calls")
    return {
        "schema_version": VERSION, "kind": "gate_b_call_receipt_handoff",
        "plan_sha256": info["plan_sha256"],
        "observations_sha256": digest_plan(observations),
        "github_source_calls": entries, "rgp_acquisition_calls": rgp,
    }


def _hex(value: object, size: int) -> bool:
    return isinstance(value, str) and len(value) == size and all(c in HEX for c in value)


def _validate_receipt(receipt: object, expected_url: str | None,
                      seen: set[str], tools: frozenset[str]) -> bool:
    if receipt is None:
        return False
    require(isinstance(receipt, dict) and set(receipt) ==
            {"call_id", "tool_name", "request_url", "response_sha256", "record_url"},
            "malformed provider receipt")
    call_id = receipt["call_id"]
    require(isinstance(call_id, str) and 8 <= len(call_id) <= 200
            and call_id not in seen, "missing or reused provider call id")
    seen.add(call_id)
    require(receipt["tool_name"] in tools, "unexpected tool type for call")
    url = receipt["request_url"]
    require(isinstance(url, str) and url.startswith("https://github.com/"),
            "untrusted or missing provider request URL")
    if expected_url is not None:
        require(url == expected_url, "provider source request differs from pinned URL")
    else:
        require(url.startswith("https://github.com/iivkis/rgp/actions/"),
                "RGP setup must reference the approved repository Actions run")
    require(_hex(receipt["response_sha256"], 64), "invalid response content digest")
    record_url = receipt["record_url"]
    require(isinstance(record_url, str) and record_url.startswith("https://")
            and len(record_url) <= 2048, "provider audit record URL missing")
    # An untrusted caller can self-author all the above strings. No GO.
    return True


def audit(plan: dict, observations: dict, ledger: dict) -> dict:
    expected = make_template(plan, observations)
    require(isinstance(ledger, dict) and set(ledger) == set(expected),
            "unexpected provider ledger fields")
    for key in ("schema_version", "kind", "plan_sha256", "observations_sha256"):
        require(ledger[key] == expected[key], f"provider ledger {key} mismatch")
    seen = set()
    counts = {}
    for key, tools in (("github_source_calls", GITHUB_SOURCE_TOOLS),
                       ("rgp_acquisition_calls", RGP_ACQUISITION_TOOLS)):
        values, expected_rows = ledger[key], expected[key]
        require(isinstance(values, list) and len(values) == len(expected_rows),
                f"{key}: missing or additional claimed calls")
        present = 0
        # Order is part of the frozen first-use and setup-slot attribution.
        for n, (entry, reference) in enumerate(zip(values, expected_rows)):
            require(isinstance(entry, dict) and set(entry) == set(reference),
                    f"{key}[{n}]: missing/extra ledger fields")
            for field in reference:
                if field != "receipt":
                    require(entry[field] == reference[field],
                            f"{key}[{n}]: claimed source/slot changed")
            url = reference.get("source_url")
            present += int(_validate_receipt(entry["receipt"], url, seen, tools))
        counts[key] = {"expected": len(expected_rows),
                       "receipts_present": present, "receipts_missing": len(expected_rows) - present}
    total = sum(v["expected"] for v in counts.values())
    found = sum(v["receipts_present"] for v in counts.values())
    return {
        "gate": "B", "status": "HOLD",
        "evidence_state": "TRACE_CANDIDATE_UNAUTHENTICATED" if found == total
                          else "INCOMPLETE_PROVIDER_RECEIPTS",
        "plan_sha256": expected["plan_sha256"],
        "observations_sha256": expected["observations_sha256"],
        **counts,
        "claimed_call_reduction": 1 - counts["rgp_acquisition_calls"]["expected"] /
                                  counts["github_source_calls"]["expected"],
        "verified_connector_call_reduction": None,
        "provider_history_authenticated": False,
        "independent_answer_grading": "NOT_VERIFIED",
        "missing_receipts_total": total - found,
        "reasons": (["provider receipts incomplete"] if found < total else []) + [
            "receipt metadata cannot authenticate itself; compare every event with original tool logs",
            "original 24 answers need independent arm-masked grading",
            "official GO requires external maintainer verification",
        ],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("template", "audit"))
    p.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    p.add_argument("--observations", type=Path,
                   default=Path("benchmarks/gate_b/observations.cache_fair_2026-10-29.json"))
    p.add_argument("--ledger", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    try:
        plan = strict_json(args.plan)
        obs = strict_json(args.observations)
        if args.command == "template":
            result = make_template(plan, obs)
        else:
            require(args.ledger is not None, "--ledger required")
            result = audit(plan, obs, strict_json(args.ledger))
    except (ProtocolError, ValueError, OSError, KeyError, TypeError) as exc:
        p.error(str(exc))
    data = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    print(data, end="")


if __name__ == "__main__":
    main()

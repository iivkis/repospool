#!/usr/bin/env python3
"""One truthful Gate B decision dossier from blinded grading + provider receipts.

This does not run an LLM or authenticate authors/host logs. The decision
remains HOLD unless a maintainer independently verifies external evidence.
Even a fully populated self-authored JSON packet can only be a candidate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gate_b import ProtocolError, strict_json
from gate_b_review import review_report
from gate_b_call_audit import audit


def decision(plan: dict, observations: dict, packet: dict,
             review: dict, ledger: dict, source_lines: dict,
             checkpoint: dict) -> dict:
    answer = review_report(plan, observations, packet, review,
                           source_lines, checkpoint)
    calls = audit(plan, observations, ledger)
    reasons = []
    if answer["status"] == "PIVOT":
        reasons.append("submitted reviewer judgments indicate a correctness or call-reduction problem; authenticity requires verification")
    elif answer["status"] != "REVIEW_CANDIDATE":
        reasons.append("all 24 independent blinded answer verdicts are not yet evidenced")
    if calls["missing_receipts_total"]:
        reasons.append(f"{calls['missing_receipts_total']} original provider call receipts missing")
    reasons.append("original provider call log and reviewer identity must be authenticated independently")
    return {
        "gate": "B", "official_status": "HOLD",
        "technical_readiness": (
            "MAINTAINER_VERIFICATION_REQUIRED"
            if answer["status"] == "REVIEW_CANDIDATE"
            and calls["missing_receipts_total"] == 0
            else "BLOCKED"
        ),
        "answer_review": answer,
        "tool_history": calls,
        "independent_provider_history_authenticated": False,
        "reviewer_identity_authenticated": False,
        "external_approval_received": False,
        "reasons": reasons,
        "next_actions": [
            "have an independent reviewer grade all 24 masked answers against pinned sources",
            "retrieve the 19 original GitHub/RGP tool events from an inspectable authenticated provider history",
            "match event IDs, requests, response digests and counts with the frozen cache-fair observations",
            "verify independence, repeated-session comparability and result before any manual GO/PIVOT decision",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, default=Path("benchmarks/gate_b/plan.json"))
    p.add_argument("--observations", type=Path, default=Path(
        "benchmarks/gate_b/observations.cache_fair_2026-10-29.json"))
    p.add_argument("--source-lines", type=Path, default=Path(
        "benchmarks/gate_b/source_line_counts.json"))
    p.add_argument("--checkpoint", type=Path, default=Path(
        "benchmarks/gate_b/corpus_preparation.json"))
    p.add_argument("--packet", type=Path, required=True)
    p.add_argument("--review", type=Path, required=True)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    try:
        report = decision(
            strict_json(args.plan), strict_json(args.observations),
            strict_json(args.packet), strict_json(args.review),
            strict_json(args.ledger), strict_json(args.source_lines),
            strict_json(args.checkpoint),
        )
    except (ProtocolError, ValueError, KeyError, TypeError, OSError) as exc:
        p.error(str(exc))
    rendered = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

"""Adversarial tests for the Gate B historical tool-call receipt handoff.

All receipts in these tests are synthetic. They must never be interpreted
as independently authenticated GitHub events or a passing product gate.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import ProtocolError  # noqa: E402
from gate_b_call_audit import audit, make_template  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text())
OBS = json.loads((ROOT / "benchmarks/gate_b/observations.cache_fair_2026-10-29.json").read_text())


def fake_receipt(index: int, url: str, kind: str) -> dict:
    return {
        "call_id": f"synthetic-call-{index:03}",
        "tool_name": "mcp__GitHub__fetch_file" if kind == "source"
                     else "mcp__GitHub__download_workflow_artifact",
        "request_url": url,
        "response_sha256": f"{index:064x}",
        "record_url": f"https://github.com/iivkis/rgp/issues/25#synthetic-{index}",
    }


def populated() -> dict:
    ledger = make_template(PLAN, OBS)
    n = 0
    for entry in ledger["github_source_calls"]:
        n += 1
        entry["receipt"] = fake_receipt(n, entry["source_url"], "source")
    urls = {
        "corpus_checkpoint": "https://github.com/iivkis/rgp/blob/main/benchmarks/gate_b/corpus_preparation.json",
        "artifact_listing": "https://github.com/iivkis/rgp/actions/runs/40767794459",
        "artifact_download": "https://github.com/iivkis/rgp/actions/runs/40767794459",
        "reader_source": "https://github.com/iivkis/rgp/blob/main/python/rgp_reader.py",
    }
    names = {
        "corpus_checkpoint": "mcp__GitHub__fetch_file",
        "artifact_listing": "mcp__GitHub__fetch_workflow_run_artifacts",
        "artifact_download": "mcp__GitHub__download_workflow_artifact",
        "reader_source": "mcp__GitHub__fetch_file",
    }
    for entry in ledger["rgp_acquisition_calls"]:
        n += 1
        purpose = entry["purpose"]
        entry["receipt"] = fake_receipt(n, urls[purpose], "rgp")
        entry["receipt"]["tool_name"] = names[purpose]
    return ledger


class GateBCallAuditTests(unittest.TestCase):
    def test_exact_frozen_13_plus_6_receipts_no_automatic_go(self):
        template = make_template(PLAN, OBS)
        self.assertEqual(len(template["github_source_calls"]), 13)
        self.assertEqual(len(template["rgp_acquisition_calls"]), 6)
        self.assertEqual(
            {repo: sum(r["repository"] == repo for r in template["rgp_acquisition_calls"])
             for repo in ("rgp", "serde_json", "itsdangerous")},
            {"rgp": 4, "serde_json": 1, "itsdangerous": 1})
        result = audit(PLAN, OBS, template)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["missing_receipts_total"], 19)
        self.assertIsNone(result["verified_connector_call_reduction"])

    def test_complete_self_reported_receipts_still_hold(self):
        result = audit(PLAN, OBS, populated())
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["evidence_state"], "TRACE_CANDIDATE_UNAUTHENTICATED")
        self.assertEqual(result["missing_receipts_total"], 0)
        self.assertFalse(result["provider_history_authenticated"])
        self.assertAlmostEqual(result["claimed_call_reduction"], 7 / 13)
        self.assertIsNone(result["verified_connector_call_reduction"])

    def test_tampered_observations_cannot_inherit_other_ledger(self):
        ledger = make_template(PLAN, OBS)
        bad = copy.deepcopy(OBS)
        bad["session_metadata"]["invented"] = True
        with self.assertRaisesRegex(ProtocolError, "observations_sha256 mismatch"):
            audit(PLAN, bad, ledger)

    def test_source_path_sha_and_first_use_attribution_locked(self):
        for field, replacement in (
            ("path", "../escape"), ("git_blob_sha1", "0" * 40),
            ("task_id", "rgp-git-snapshot"),
            ("source_url", "https://github.com/wrong/source"),
        ):
            with self.subTest(field=field):
                ledger = populated()
                ledger["github_source_calls"][0][field] = replacement
                with self.assertRaisesRegex(ProtocolError, "claimed source/slot changed"):
                    audit(PLAN, OBS, ledger)

    def test_rejects_duplicate_provider_event(self):
        ledger = populated()
        ledger["rgp_acquisition_calls"][0]["receipt"]["call_id"] = (
            ledger["github_source_calls"][0]["receipt"]["call_id"])
        with self.assertRaisesRegex(ProtocolError, "reused provider call"):
            audit(PLAN, OBS, ledger)

    def test_rejects_changed_request_invalid_sha_and_malformed_receipt(self):
        for field, bad, fragment in (
            ("request_url", "https://github.com/other/thing", "request differs"),
            ("response_sha256", "not-sha", "response content digest"),
            ("record_url", "no-record", "audit record URL"),
            ("tool_name", "mcp__GitHub__merge_pull_request", "unexpected tool"),
        ):
            with self.subTest(field=field):
                ledger = populated()
                ledger["github_source_calls"][0]["receipt"][field] = bad
                with self.assertRaisesRegex(ProtocolError, fragment):
                    audit(PLAN, OBS, ledger)
        ledger = populated()
        ledger["github_source_calls"][0]["receipt"]["fabricated"] = True
        with self.assertRaisesRegex(ProtocolError, "malformed provider receipt"):
            audit(PLAN, OBS, ledger)

    def test_rejects_free_or_extra_operations(self):
        for kind in ("github_source_calls", "rgp_acquisition_calls"):
            ledger = populated()
            ledger[kind].pop()
            with self.subTest(kind=kind), self.assertRaisesRegex(ProtocolError, "claimed calls"):
                audit(PLAN, OBS, ledger)
        ledger = populated()
        ledger["github_source_calls"].append(copy.deepcopy(ledger["github_source_calls"][0]))
        with self.assertRaisesRegex(ProtocolError, "claimed calls"):
            audit(PLAN, OBS, ledger)

    def test_rejects_unpinned_rgp_setup_and_tool_swap(self):
        ledger = populated()
        ledger["rgp_acquisition_calls"][2]["receipt"]["request_url"] = (
            "https://github.com/serde-rs/json/actions/runs/40767794459")
        with self.assertRaisesRegex(ProtocolError, "approved repository"):
            audit(PLAN, OBS, ledger)
        ledger = populated()
        ledger["rgp_acquisition_calls"][2]["receipt"]["tool_name"] = "mcp__GitHub__fetch_blob"
        with self.assertRaisesRegex(ProtocolError, "unexpected tool"):
            audit(PLAN, OBS, ledger)

    def test_incomplete_receipt_does_not_count_as_zero_cost(self):
        ledger = populated()
        ledger["github_source_calls"][2]["receipt"] = None
        result = audit(PLAN, OBS, ledger)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["missing_receipts_total"], 1)
        self.assertIsNone(result["verified_connector_call_reduction"])


if __name__ == "__main__":
    unittest.main()

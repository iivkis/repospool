"""Adversarial accounting checks for a cache-aware Gate B acquisition replay.

The ledger is assistant reported; these tests do not attest real connector history.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import ProtocolError, evaluate  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text(encoding="utf-8"))
OBS = json.loads((ROOT / "benchmarks/gate_b/observations.cache_fair_2026-10-29.json").read_text(encoding="utf-8"))


class CacheFairGateB(unittest.TestCase):
    def test_measured_first_use_replay_is_a_candidate_not_a_go(self):
        report = evaluate(PLAN, OBS)
        self.assertEqual(report["status"], "GO_CANDIDATE")
        self.assertEqual(report["received_observations"], 24)
        self.assertEqual(report["github_calls"], 13)
        self.assertEqual(report["rgp_calls_including_preparation"], 6)
        self.assertAlmostEqual(report["call_reduction"], 7 / 13)
        self.assertEqual(report["github_cache"]["unique_source_fetches"], 13)
        self.assertIn("self_reported", report["github_cache"]["evidence_status"])
        self.assertIsNone(report["token_reduction"])
        self.assertEqual(OBS["session_metadata"]["status"], "HOLD_pending_independent_review")

    def test_cached_zero_call_task_is_allowed_only_with_full_ledger(self):
        self.assertEqual(next(x for x in OBS["observations"] if
                              x["task_id"] == "rgp-git-snapshot" and
                              x["arm"] == "github_connector")["connector_calls"], 0)
        x = copy.deepcopy(OBS)
        x.pop("github_cache")
        with self.assertRaisesRegex(ProtocolError, "source retrieval call"):
            evaluate(PLAN, x)

    def test_cache_rejects_missing_duplicate_or_unplanned_fetch(self):
        for mode in ("missing", "duplicate", "unplanned"):
            x = copy.deepcopy(OBS)
            refs = x["github_cache"]["source_fetches"]
            if mode == "missing":
                refs.pop()
            elif mode == "duplicate":
                refs[-1] = copy.deepcopy(refs[0])
            else:
                refs[0]["path"] = "not-in-frozen-plan.rs"
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    ProtocolError, "cache ledger|unknown or duplicate pinned source"):
                evaluate(PLAN, x)

    def test_cache_rejects_laundered_first_use_or_zero_calls(self):
        x = copy.deepcopy(OBS)
        x["github_cache"]["source_fetches"][0]["task_id"] = "rgp-git-snapshot"
        with self.assertRaisesRegex(ProtocolError, "first task use"):
            evaluate(PLAN, x)
        x = copy.deepcopy(OBS)
        github = next(row for row in x["observations"] if row["arm"] == "github_connector"
                      and row["task_id"] == "serde-json-deserializer")
        github["connector_calls"] = 0
        with self.assertRaisesRegex(ProtocolError, "cache accounting mismatch"):
            evaluate(PLAN, x)

    def test_cache_rejects_wrong_sha_and_wrong_url(self):
        for key, bad, expected in (
            ("git_blob_sha1", "xyz", "Git blob SHA-1"),
            ("source_url", "https://github.com/another/repo", "source URL"),
        ):
            x = copy.deepcopy(OBS)
            x["github_cache"]["source_fetches"][0][key] = bad
            with self.subTest(key=key), self.assertRaisesRegex(ProtocolError, expected):
                evaluate(PLAN, x)

    def test_cache_rejects_source_content_mismatch_and_missing_rgp_evidence(self):
        x = copy.deepcopy(OBS)
        x["observations"][0]["source_evidence"]["src/main.rs"]["git_blob_sha1"] = "a" * 40
        with self.assertRaisesRegex(ProtocolError, "source anchor mismatch"):
            evaluate(PLAN, x)
        x = copy.deepcopy(OBS)
        rgp = next(row for row in x["observations"] if row["arm"] == "rgp_reader")
        rgp.pop("source_evidence")
        with self.assertRaisesRegex(ProtocolError, "source evidence missing"):
            evaluate(PLAN, x)

    def test_cache_rejects_wrong_policy_and_unhashable_fields(self):
        for key, bad in (("policy", "offset_unbounded"), ("source_fetches", {})):
            x = copy.deepcopy(OBS)
            x["github_cache"][key] = bad
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                evaluate(PLAN, x)
        x = copy.deepcopy(OBS)
        x["github_cache"]["source_fetches"][0]["path"] = []
        with self.assertRaisesRegex(ProtocolError, "invalid field type"):
            evaluate(PLAN, x)


if __name__ == "__main__":
    unittest.main()

"""Conditional Gate B call-volume calculations, not independent trials."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import ProtocolError  # noqa: E402
from gate_b_sensitivity import analyze, task_subset_counts, validate_setup  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text(encoding="utf-8"))
OBS = json.loads((ROOT / "benchmarks/gate_b/observations.cache_fair_2026-10-29.json")
                 .read_text(encoding="utf-8"))
SETUP = json.loads((ROOT / "benchmarks/gate_b/setup_cost_model.json")
                   .read_text(encoding="utf-8"))


class GateBSensitivity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = analyze(PLAN, OBS, SETUP)
        cls.by_size = {x["tasks"]: x for x in cls.report["by_task_count"]}

    def test_reproduces_full_measured_baseline_without_new_trials(self):
        self.assertEqual(self.report["full_run_measured_calls"], {"github": 13, "rgp": 6})
        self.assertAlmostEqual(self.report["full_run_reduction_pct"], 100 * 7 / 13)
        self.assertEqual(self.report["new_experimental_trials"], 0)
        self.assertTrue(self.report["decision"].startswith("HOLD"))
        self.assertEqual(self.report["total_nonempty_subsets"], 4095)
        self.assertEqual(self.report["conditional_shared_setup_calls"], 3)

    def test_single_task_always_worse_due_to_amortized_preparation(self):
        x = self.by_size[1]
        self.assertEqual(x["subsets"], 12)
        self.assertEqual(x["github_fewer_calls"], 12)
        self.assertEqual(x["rgp_fewer_calls"], 0)
        self.assertEqual(x["at_least_30_percent"], 0)

    def test_threshold_depends_on_workload_size_and_files(self):
        self.assertEqual(self.by_size[3]["at_least_30_percent"], 0)
        self.assertEqual(self.by_size[4]["at_least_30_percent"], 2)
        self.assertEqual(self.by_size[5]["rgp_fewer_calls"], 521)
        self.assertEqual(self.by_size[5]["subsets"], 792)
        self.assertEqual(self.by_size[5]["at_least_30_percent"], 51)
        for size in range(9, 13):
            x = self.by_size[size]
            self.assertEqual(x["at_least_30_percent"], x["subsets"])
        self.assertEqual(self.by_size[12]["subsets"], 1)

    def test_overlap_is_deduplicated_by_repository_and_path(self):
        by_id = {t["id"]: t for t in PLAN["tasks"]}
        rows = [by_id["rgp-transport-hash"], by_id["rgp-git-snapshot"]]
        cost = task_subset_counts(tuple(rows), 3, {k: 1 for k in
                                  ("rgp", "serde_json", "itsdangerous")},
                                  {x["id"]: 0 for x in PLAN["tasks"]})
        self.assertEqual(cost, (2, 4))

    def test_cost_model_disagrees_with_recorded_acquisition(self):
        x = copy.deepcopy(SETUP)
        x["shared_preparation_calls"]["pinned_reader_source"] = 0
        with self.assertRaisesRegex(ProtocolError, "positive integers"):
            analyze(PLAN, OBS, x)
        x = copy.deepcopy(SETUP)
        x["shared_preparation_calls"]["pinned_reader_source"] = 2
        with self.assertRaisesRegex(ProtocolError, "disagree"):
            analyze(PLAN, OBS, x)

    def test_model_rejects_fabricated_free_preparation(self):
        x = copy.deepcopy(SETUP)
        x["artifact_download_calls_per_repository"]["rgp"] = 0
        with self.assertRaisesRegex(ProtocolError, "positive integers"):
            analyze(PLAN, OBS, x)
        x = copy.deepcopy(SETUP)
        x["shared_preparation_calls"]["corpus_checkpoint"] = False
        with self.assertRaisesRegex(ProtocolError, "positive integers"):
            analyze(PLAN, OBS, x)

    def test_model_rejects_unknown_missing_repositories(self):
        for mode in ("extra", "missing"):
            x = copy.deepcopy(SETUP)
            downloads = x["artifact_download_calls_per_repository"]
            if mode == "extra":
                downloads["phantom"] = 1
            else:
                downloads.pop("serde_json")
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    ProtocolError, "missing repository acquisition costs"):
                analyze(PLAN, OBS, x)

    def test_broken_cached_source_ledger_cannot_feed_sensitivity(self):
        x = copy.deepcopy(OBS)
        x["github_cache"]["source_fetches"].pop()
        with self.assertRaises(ProtocolError):
            analyze(PLAN, x, SETUP)
        x = copy.deepcopy(OBS)
        x.pop("github_cache")
        with self.assertRaises(ProtocolError):
            analyze(PLAN, x, SETUP)

    def test_plan_mismatch_and_unmeasured_costs_are_rejected(self):
        x = copy.deepcopy(OBS)
        x["plan_sha256"] = "0" * 64
        with self.assertRaises(ProtocolError):
            analyze(PLAN, x, SETUP)
        x = copy.deepcopy(OBS)
        x["rgp_preparation_calls"]["rgp"] = 0
        with self.assertRaises(ProtocolError):
            analyze(PLAN, x, SETUP)

    def test_summary_distinguishes_ties_from_savings(self):
        for row in self.report["by_task_count"]:
            self.assertEqual(row["rgp_fewer_calls"] + row["github_fewer_calls"]
                             + row["same_calls"], row["subsets"])
            self.assertGreaterEqual(row["max_reduction_pct"], row["min_reduction_pct"])
            self.assertLessEqual(row["at_least_30_percent"], row["rgp_fewer_calls"])


if __name__ == "__main__":
    unittest.main()

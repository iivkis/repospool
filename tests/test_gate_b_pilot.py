"""Schema and accounting checks for the single-session Gate B diagnostic.

These tests do NOT authenticate connector traces or independently grade answers.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import evaluate, digest_plan  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text(encoding="utf-8"))
OBS = json.loads((ROOT / "benchmarks/gate_b/observations.pilot_2026-10-29.json").read_text(encoding="utf-8"))


class GateBSingleSessionPilot(unittest.TestCase):
    def test_experimental_observations_are_structurally_complete(self):
        report = evaluate(PLAN, OBS)
        self.assertEqual(OBS["plan_sha256"], digest_plan(PLAN))
        self.assertEqual(report["status"], "GO_CANDIDATE")
        self.assertEqual(report["received_observations"], 24)
        self.assertEqual(report["github_calls"], 18)
        self.assertEqual(report["rgp_calls_including_preparation"], 6)
        self.assertAlmostEqual(report["call_reduction"], 12 / 18)
        self.assertEqual(report["correctness_regressions"], [])
        self.assertEqual(report["rgp_incorrect_tasks"], [])
        self.assertIsNone(report["token_reduction"])

    def test_all_source_links_are_commit_pinned_and_match_rubric(self):
        repos = {r["id"]: r for r in PLAN["repositories"]}
        tasks = {t["id"]: t for t in PLAN["tasks"]}
        for row in OBS["observations"]:
            task = tasks[row["task_id"]]
            repo = repos[row["repository"]]
            paths = task["reference_paths"]
            self.assertEqual(row["source_paths"], paths)
            self.assertEqual(set(row["source_evidence"]), set(paths))
            for path in paths:
                source = row["source_evidence"][path]
                self.assertEqual(
                    source["source_url"],
                    f'https://github.com/{repo["github"]}/blob/{repo["commit"]}/{path}',
                )
                self.assertEqual(len(source["git_blob_sha1"]), 40)
            self.assertEqual(row["evidence_url"], row["source_evidence"][paths[0]]["source_url"])

    def test_metadata_marks_this_diagnostic_as_not_a_final_decision(self):
        metadata = OBS["session_metadata"]
        self.assertEqual(metadata["kind"], "single_session_unblinded_diagnostic")
        self.assertEqual(metadata["status"], "awaiting_independent_review")
        self.assertTrue(any("GO_CANDIDATE" in s for s in metadata["limitations"]))


if __name__ == "__main__":
    unittest.main()

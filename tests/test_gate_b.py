"""No model runtime: only data/experiment accounting tests."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import ProtocolError, digest_plan, evaluate, validate_plan  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text())


def samples(base=4, local=1, base_correct=True, local_correct=True):
    result = {"schema_version": "2.0", "method": "assistant_in_chat",
              "plan_sha256": digest_plan(PLAN), "rgp_preparation_calls":
              {r["id"]: 1 for r in PLAN["repositories"]}, "observations": []}
    commits = {r["id"]: r["commit"] for r in PLAN["repositories"]}
    for task in PLAN["tasks"]:
        for arm in PLAN["arms"]:
            is_base = arm == "github_connector"
            result["observations"].append({
                "task_id": task["id"], "arm": arm,
                "repository": task["repository"],
                "source_commit": commits[task["repository"]],
                "correct": base_correct if is_base else local_correct,
                "connector_calls": base if is_base else local,
                "answer": "Example answer derived from source files",
                "evidence_url": "https://github.com/iivkis/rgp/blob/main/README.md",
                "source_paths": task["reference_paths"],
            })
    return result


class AssistantOnlyGateB(unittest.TestCase):
    def test_12_tasks_three_pinned_repositories(self):
        summary = validate_plan(PLAN)
        self.assertEqual(summary["repositories"], 3)
        self.assertEqual(summary["tasks"], 12)
        self.assertEqual(summary["expected_observations"], 24)

    def test_empty_template_is_hold(self):
        x = json.loads((ROOT / "benchmarks/gate_b/observations.template.json").read_text())
        self.assertEqual(evaluate(PLAN, x)["status"], "HOLD")
        self.assertEqual(evaluate(PLAN, x)["received_observations"], 0)

    def test_complete_evidence_yields_candidate_not_automatic_go(self):
        x = evaluate(PLAN, samples())
        self.assertEqual(x["status"], "GO_CANDIDATE")
        self.assertEqual(x["github_calls"], 48)
        self.assertEqual(x["rgp_calls_including_preparation"], 15)
        self.assertIsNone(x["token_reduction"])

    def test_missing_pair_never_scores(self):
        x = samples()
        x["observations"].pop()
        self.assertEqual(evaluate(PLAN, x)["status"], "HOLD")

    def test_preparation_cost_cannot_be_ignored(self):
        x = samples(base=2, local=2)
        self.assertEqual(evaluate(PLAN, x)["status"], "PIVOT")
        x.pop("rgp_preparation_calls")
        self.assertEqual(evaluate(PLAN, x)["status"], "HOLD")

    def test_correctness_regression_overrides_savings(self):
        x = evaluate(PLAN, samples(local_correct=False))
        self.assertEqual(x["status"], "PIVOT")
        self.assertEqual(len(x["correctness_regressions"]), 12)

    def test_plan_mismatch_is_hold(self):
        x = samples()
        x["plan_sha256"] = "0" * 64
        self.assertEqual(evaluate(PLAN, x)["status"], "HOLD")

    def test_unknown_model_method_not_allowed(self):
        x = samples()
        x["method"] = "external_model"
        self.assertEqual(evaluate(PLAN, x)["status"], "HOLD")
        p = copy.deepcopy(PLAN)
        p["method"] = "external_model"
        with self.assertRaisesRegex(ProtocolError, "external model"):
            validate_plan(p)

    def test_reject_duplicate_or_unknown_task(self):
        x = samples()
        x["observations"].append(copy.deepcopy(x["observations"][0]))
        with self.assertRaisesRegex(ProtocolError, "duplicate observation"):
            evaluate(PLAN, x)
        x = samples()
        x["observations"][0]["task_id"] = "fake"
        with self.assertRaisesRegex(ProtocolError, "invalid task"):
            evaluate(PLAN, x)

    def test_reject_unmeasured_or_negative_calls(self):
        for bad in (None, -1, 0.3, False):
            x = samples()
            x["observations"][0]["connector_calls"] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(ProtocolError, "connector_calls"):
                evaluate(PLAN, x)

    def test_reject_unpinned_revision_and_mismatched_paths(self):
        x = samples()
        x["observations"][0]["source_commit"] = "HEAD"
        with self.assertRaisesRegex(ProtocolError, "wrong commit"):
            evaluate(PLAN, x)
        x = samples()
        x["observations"][0]["source_paths"] = ["not/in/task"]
        with self.assertRaisesRegex(ProtocolError, "source paths"):
            evaluate(PLAN, x)

    def test_missing_answer_and_evidence_rejected(self):
        x = samples()
        x["observations"][0]["answer"] = ""
        with self.assertRaisesRegex(ProtocolError, "answer missing"):
            evaluate(PLAN, x)
        x = samples()
        x["observations"][0]["evidence_url"] = ""
        with self.assertRaisesRegex(ProtocolError, "evidence"):
            evaluate(PLAN, x)


if __name__ == "__main__":
    unittest.main()

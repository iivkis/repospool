"""Gate B result scoring is deliberately fail-closed. These are fake unit trials."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import ProtocolError, digest_plan, evaluate, strict_json, validate_plan  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text())


def make_trials(*, baseline_calls=10, rgp_calls=6, baseline_tokens=1000,
                rgp_tokens=900, baseline_correct=True, rgp_correct=True):
    result = {
        "schema_version": "1.0", "experiment_kind": "real_agent", "protocol_version": "1.0",
        "plan_sha256": digest_plan(PLAN), "model_id": "test-model-not-real",
        "token_budget": 20000, "transport_verified": True, "trials": [],
        "collector": "gate_b_capture/1.0", "trace_sha256_by_filename": {},
    }
    commits = {r["id"]: r["commit"] for r in PLAN["repositories"]}
    for task in PLAN["tasks"]:
        for rep in range(1, 4):
            for arm in ("github_connector", "rgp_actions_artifact"):
                baseline = arm == "github_connector"
                name = f'{task["id"]}-{rep}-{arm}.json'
                trace_sha = hashlib.sha256(name.encode("utf-8")).hexdigest()
                result["trace_sha256_by_filename"][name] = trace_sha
                result["trials"].append({
                    "trace_sha256": trace_sha,
                    "task_id": task["id"], "repository": task["repository"],
                    "source_commit": commits[task["repository"]], "repetition": rep,
                    "arm": arm, "model_id": result["model_id"], "token_budget": 20000,
                    "correct": baseline_correct if baseline else rgp_correct,
                    "connector_calls": baseline_calls if baseline else rgp_calls,
                    "connector_transferred_bytes": 20000 if baseline else 15000,
                    "total_tokens": baseline_tokens if baseline else rgp_tokens,
                    "duration_ms": 2000 if baseline else 3000,
                    "preparation_ms": 0 if baseline else 1000,
                    "preparation_bytes": 0 if baseline else 9000,
                    "peak_memory_bytes": 300000, "missed_dependencies": 0,
                    "false_positives": 0, "errors": [],
                    "evidence_url": "https://example.test/fixture/not-a-measurement",
                })
    return result


class GateBTest(unittest.TestCase):
    def test_pinned_plan(self):
        info = validate_plan(PLAN)
        self.assertEqual(info["tasks"], 12)
        self.assertEqual(info["repositories"], 3)
        self.assertIn("serde_json", {r["id"] for r in PLAN["repositories"]})
        self.assertEqual(len(info["plan_sha256"]), 64)

    def test_default_template_cannot_pass(self):
        template = strict_json(ROOT / "benchmarks/gate_b/trials.template.json")
        result = evaluate(PLAN, template)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["received_trials"], 0)
        self.assertEqual(result["expected_trials"], 72)

    def test_matched_calls_eligible_in_synthetic_unit_test(self):
        outcome = evaluate(PLAN, make_trials())
        self.assertEqual(outcome["status"], "GO")
        self.assertAlmostEqual(outcome["call_reduction"], 0.4)
        self.assertEqual(outcome["arms"]["rgp_actions_artifact"]["n"], 36)
        self.assertEqual(outcome["arms"]["github_connector"]["duration_ms"]["median"], 2000)

    def test_tokens_threshold_alternative(self):
        trials = make_trials(rgp_calls=9, rgp_tokens=790)
        outcome = evaluate(PLAN, trials)
        self.assertEqual(outcome["status"], "GO")
        self.assertGreater(outcome["token_reduction"], 0.20)

    def test_no_efficiency_is_pivot(self):
        result = evaluate(PLAN, make_trials(rgp_calls=9, rgp_tokens=950))
        self.assertEqual(result["status"], "PIVOT")

    def test_correctness_regression_overrides_savings(self):
        result = evaluate(PLAN, make_trials(rgp_calls=2, rgp_tokens=100, rgp_correct=False))
        self.assertEqual(result["status"], "PIVOT")
        self.assertEqual(len(result["correctness_regressions"]), 36)

    def test_missing_pair_is_hold(self):
        trials = make_trials()
        trials["trials"].pop()
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_unverified_transport_is_hold(self):
        trials = make_trials()
        trials["transport_verified"] = False
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_wrong_preregistered_plan_is_hold(self):
        trials = make_trials()
        trials["plan_sha256"] = "0" * 64
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_simulated_trials_are_hold(self):
        trials = make_trials()
        trials["experiment_kind"] = "synthetic"
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_duplicate_trial_rejected(self):
        trials = make_trials()
        trials["trials"].append(copy.deepcopy(trials["trials"][0]))
        with self.assertRaisesRegex(ProtocolError, "duplicate trial"):
            evaluate(PLAN, trials)

    def test_raw_table_without_collector_cannot_go(self):
        trials = make_trials()
        trials.pop("collector")
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_missing_trace_map_cannot_go(self):
        trials = make_trials()
        trials.pop("trace_sha256_by_filename")
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_mismatched_trace_checksum_cannot_go(self):
        trials = make_trials()
        trials["trials"][0]["trace_sha256"] = "f" * 64
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_reused_trace_checksum_cannot_go(self):
        trials = make_trials()
        trials["trials"][0]["trace_sha256"] = trials["trials"][1]["trace_sha256"]
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_malformed_trace_digest_type_cannot_go(self):
        trials = make_trials()
        trials["trials"][0]["trace_sha256"] = ["not-a-digest"]
        self.assertEqual(evaluate(PLAN, trials)["status"], "HOLD")

    def test_wrong_model_rejected(self):
        trials = make_trials()
        trials["trials"][0]["model_id"] = "other-model"
        with self.assertRaisesRegex(ProtocolError, "model or token budget mismatch"):
            evaluate(PLAN, trials)

    def test_missing_cost_rejected(self):
        trials = make_trials()
        del trials["trials"][0]["preparation_ms"]
        with self.assertRaisesRegex(ProtocolError, "preparation_ms"):
            evaluate(PLAN, trials)

    def test_wrong_commit_rejected(self):
        trials = make_trials()
        trials["trials"][0]["source_commit"] = "f" * 40
        with self.assertRaisesRegex(ProtocolError, "wrong source commit"):
            evaluate(PLAN, trials)

    def test_invalid_plan_duplicate_task(self):
        bad = copy.deepcopy(PLAN)
        bad["tasks"].append(copy.deepcopy(bad["tasks"][0]))
        with self.assertRaisesRegex(ProtocolError, "duplicate task"):
            validate_plan(bad)


if __name__ == "__main__":
    unittest.main()

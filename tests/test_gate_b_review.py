"""Adversarial checks for arm-masked review packets. Verdicts here are synthetic.

No tests provide independent grading, authentic reviewer identity or true GO.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import ProtocolError, digest_plan  # noqa: E402
from gate_b_review import (blank_review, make_packet, review_report,
                           validate_packet)  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text(encoding="utf-8"))
OBS = json.loads((ROOT / "benchmarks/gate_b/observations.cache_fair_2026-10-29.json")
                 .read_text(encoding="utf-8"))


def filled(packet):
    review = blank_review(packet)
    review["reviewer"] = {
        "id": "synthetic-reviewer-for-unit-tests-only",
        "independent_of_observation_author": True,
        "review_record_url": "https://github.com/iivkis/rgp/issues/25",
    }
    paths = {task["task_id"]: [ref["path"] for ref in task["source_refs"]]
             for task in packet["tasks"]}
    for row in review["judgments"]:
        row["correct"] = True
        row["rationale"] = "Synthetic test verdict, not a real source review."
        row["evidence"] = [
            {"path": path, "start_line": 1, "end_line": 2}
            for path in paths[row["task_id"]]
        ]
    return review


def change_grade(packet, review, task_id, arm, verdict):
    answer = next(row["answer"] for row in OBS["observations"]
                  if row["task_id"] == task_id and row["arm"] == arm)
    item = next(task for task in packet["tasks"] if task["task_id"] == task_id)
    case = next(a["case_id"] for a in item["answers"] if a["answer"] == answer)
    next(row for row in review["judgments"] if row["task_id"] == task_id
         and row["case_id"] == case)["correct"] = verdict


class MaskedReview(unittest.TestCase):
    def setUp(self):
        self.packet = make_packet(PLAN, OBS)
        self.review = filled(self.packet)

    def test_packet_preserves_pinned_answers_without_exposing_arms(self):
        validate_packet(PLAN, OBS, self.packet)
        self.assertEqual(len(self.packet["tasks"]), 12)
        self.assertEqual(self.packet["observations_sha256"], digest_plan(OBS))
        for task in self.packet["tasks"]:
            self.assertEqual(len(task["answers"]), 2)
            for answer in task["answers"]:
                self.assertNotIn("arm", answer)
                self.assertNotIn("correct", answer)
                self.assertNotIn("connector_calls", answer)

    def test_empty_template_fails_closed(self):
        result = review_report(PLAN, OBS, self.packet, blank_review(self.packet))
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["missing"], 24)
        self.assertEqual(result["reviewed"], 0)

    def test_synthetic_complete_review_is_only_candidate(self):
        result = review_report(PLAN, OBS, self.packet, self.review)
        self.assertEqual(result["status"], "REVIEW_CANDIDATE")
        self.assertEqual(result["reviewed"], 24)
        self.assertEqual(result["connector_calls"], {"github": 13, "rgp": 6})
        self.assertAlmostEqual(result["call_reduction"], 7 / 13)
        self.assertEqual(result["reviewer_independence"], "self_declared_unverified")
        self.assertNotEqual(result["status"], "GO")

    def test_false_reviewed_rgp_answer_is_pivot(self):
        change_grade(self.packet, self.review, "serde-json-deserializer", "rgp_reader", False)
        result = review_report(PLAN, OBS, self.packet, self.review)
        self.assertEqual(result["status"], "PIVOT")
        self.assertEqual(result["correctness_regressions"], ["serde-json-deserializer"])
        self.assertEqual(result["rgp_incorrect_tasks"], ["serde-json-deserializer"])

    def test_two_wrong_answers_still_pivot(self):
        for arm in ("github_connector", "rgp_reader"):
            change_grade(self.packet, self.review, "serde-json-deserializer", arm, False)
        result = review_report(PLAN, OBS, self.packet, self.review)
        self.assertEqual(result["status"], "PIVOT")
        self.assertEqual(result["correctness_regressions"], [])
        self.assertEqual(result["rgp_incorrect_tasks"], ["serde-json-deserializer"])

    def test_reject_packet_answer_substitution(self):
        corrupt = copy.deepcopy(self.packet)
        corrupt["tasks"][0]["answers"][0]["answer"] = "not from source"
        with self.assertRaisesRegex(ProtocolError, "digest mismatch"):
            validate_packet(PLAN, OBS, corrupt)
        corrupt["tasks"][0]["answers"][0]["answer_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProtocolError, "digest mismatch"):
            validate_packet(PLAN, OBS, corrupt)

    def test_reject_packet_source_link_or_plan_hash_tampering(self):
        corrupt = copy.deepcopy(self.packet)
        corrupt["tasks"][0]["source_refs"][0]["url"] = "https://github.com/wrong"
        with self.assertRaisesRegex(ProtocolError, "altered source_refs"):
            validate_packet(PLAN, OBS, corrupt)
        corrupt = copy.deepcopy(self.packet)
        corrupt["plan_sha256"] = "a" * 64
        with self.assertRaisesRegex(ProtocolError, "provenance mismatch"):
            validate_packet(PLAN, OBS, corrupt)

    def test_review_bound_to_exact_packet(self):
        corrupt = copy.deepcopy(self.review)
        corrupt["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProtocolError, "not bound"):
            review_report(PLAN, OBS, self.packet, corrupt)

    def test_partial_review_is_hold_not_pivot(self):
        self.review["judgments"][0]["correct"] = None
        result = review_report(PLAN, OBS, self.packet, self.review)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["missing"], 1)

    def test_independence_is_required_but_not_authenticated(self):
        self.review["reviewer"]["independent_of_observation_author"] = False
        self.assertEqual(review_report(PLAN, OBS, self.packet, self.review)["status"], "HOLD")
        self.review["reviewer"]["independent_of_observation_author"] = True
        self.review["reviewer"]["id"] = "assistant-in-chat"
        self.assertEqual(review_report(PLAN, OBS, self.packet, self.review)["status"], "HOLD")
        self.review["reviewer"]["id"] = "unit-test-reviewer"
        self.review["reviewer"]["review_record_url"] = ""
        self.assertEqual(review_report(PLAN, OBS, self.packet, self.review)["status"], "HOLD")

    def test_bad_evidence_path_line_or_rationale_rejected(self):
        for wrong, error in (
            ({"path": "../wrong", "start_line": 1, "end_line": 2}, "invalid source line evidence"),
            ({"path": "src/main.rs", "start_line": 0, "end_line": 2}, "invalid source line evidence"),
        ):
            r = copy.deepcopy(self.review)
            r["judgments"][0]["evidence"] = [wrong]
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ProtocolError, error):
                review_report(PLAN, OBS, self.packet, r)
        r = copy.deepcopy(self.review)
        r["judgments"][0]["rationale"] = "OK"
        with self.assertRaisesRegex(ProtocolError, "substantive rationale"):
            review_report(PLAN, OBS, self.packet, r)

    def test_missing_coverage_and_duplicate_judgment_rejected(self):
        r = copy.deepcopy(self.review)
        r["judgments"][0]["evidence"].pop()
        with self.assertRaisesRegex(ProtocolError, "not all required source paths"):
            review_report(PLAN, OBS, self.packet, r)
        r = copy.deepcopy(self.review)
        r["judgments"].append(copy.deepcopy(r["judgments"][0]))
        with self.assertRaisesRegex(ProtocolError, "unknown or duplicate case"):
            review_report(PLAN, OBS, self.packet, r)

    def test_inconsistent_verdicts_for_identical_answers_rejected(self):
        duplicate = copy.deepcopy(OBS)
        a = next(row for row in duplicate["observations"] if row["task_id"] == "rgp-git-snapshot"
                 and row["arm"] == "github_connector")
        b = next(row for row in duplicate["observations"] if row["task_id"] == "rgp-git-snapshot"
                 and row["arm"] == "rgp_reader")
        b["answer"] = a["answer"]
        packet = make_packet(PLAN, duplicate)
        review = filled(packet)
        first = next(j for j in review["judgments"] if j["task_id"] == "rgp-git-snapshot"
                     and j["case_id"] == "A")
        first["correct"] = False
        with self.assertRaisesRegex(ProtocolError, "conflicting grades"):
            review_report(PLAN, duplicate, packet, review)


if __name__ == "__main__":
    unittest.main()

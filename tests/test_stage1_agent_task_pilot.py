"""Frozen 12-task context plan and explicit no-answer-claim guardrails."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "python"))
from stage1_agent_task_pilot import TERMS  # noqa: E402
from rgp_agent_context import context, compact_json  # noqa: E402
from test_reader_api import three_scope_artifact  # noqa: E402
from rgp_reader import Reader  # noqa: E402


class FrozenTaskPilotTests(unittest.TestCase):
    def test_exact_pre_registered_twelve_tasks(self):
        plan = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text())
        self.assertEqual(set(TERMS), {t["id"] for t in plan["tasks"]})
        self.assertEqual(len(TERMS), 12)
        for task in plan["tasks"]:
            with self.subTest(task=task["id"]):
                terms = TERMS[task["id"]]
                self.assertTrue(1 <= len(terms) <= 6)
                self.assertEqual(len(terms), len(set(terms)))
                self.assertTrue(all(isinstance(t, str) and 1 <= len(t.encode()) <= 128
                                    for t in terms))
        self.assertEqual(
            {r: sum(t["repository"] == r for t in plan["tasks"])
             for r in ("rgp", "serde_json", "itsdangerous")},
            {"rgp": 4, "serde_json": 4, "itsdangerous": 4})

    def test_missing_term_is_not_disguised_as_source_evidence(self):
        result = context(Reader(three_scope_artifact()),
                         ["needle", "SOME_NOT_EXISTING_METHOD"], max_bytes=2048)
        self.assertEqual(result["terms_without_source"], ["SOME_NOT_EXISTING_METHOD"])
        self.assertTrue(result["sources"])
        self.assertLessEqual(len(compact_json(result).encode("utf-8")), 2048)

    def test_each_requested_term_gets_evidence_before_repeats(self):
        reader = Reader(three_scope_artifact())
        result = context(reader, ["needle", "again"], prefix="src/")
        self.assertEqual(result["terms_without_source"], [])
        selected = {s["match_term"] for s in result["sources"]
                    if s["path"] == "src/a.py"}
        self.assertEqual(selected, {"needle", "again"})


if __name__ == "__main__":
    unittest.main()

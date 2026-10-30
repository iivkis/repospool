"""Decision-dossier tests: synthetic evidence never yields an automatic Gate B GO."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b_review import make_packet, blank_review  # noqa: E402
from gate_b_call_audit import make_template  # noqa: E402
from gate_b_decision import decision  # noqa: E402

def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))

PLAN = load("benchmarks/gate_b/plan.json")
OBS = load("benchmarks/gate_b/observations.cache_fair_2026-10-29.json")
LINES = load("benchmarks/gate_b/source_line_counts.json")
CHECKPOINT = load("benchmarks/gate_b/corpus_preparation.json")


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.packet = make_packet(PLAN, OBS)
        self.review = blank_review(self.packet)
        self.ledger = make_template(PLAN, OBS)

    def check(self):
        return decision(PLAN, OBS, self.packet, self.review,
                        self.ledger, LINES, CHECKPOINT)

    def test_unreviewed_corpus_has_24_missing_verdicts_and_19_missing_receipts(self):
        report = self.check()
        self.assertEqual(report["official_status"], "HOLD")
        self.assertEqual(report["technical_readiness"], "BLOCKED")
        self.assertEqual(report["answer_review"]["missing"], 24)
        self.assertEqual(report["tool_history"]["missing_receipts_total"], 19)
        self.assertFalse(report["independent_provider_history_authenticated"])

    def test_independent_review_claim_without_verdicts_still_hold(self):
        self.review["reviewer"] = {
            "id": "synthetic-reviewed-by-someone",
            "independent_of_observation_author": True,
            "review_record_url": "https://github.com/iivkis/rgp/issues/25",
        }
        self.assertEqual(self.check()["official_status"], "HOLD")
        self.assertEqual(self.check()["technical_readiness"], "BLOCKED")

    def test_packet_tampering_rejected(self):
        self.review["packet_sha256"] = "0" * 64
        from gate_b import ProtocolError
        with self.assertRaisesRegex(ProtocolError, "not bound"):
            self.check()

    def test_ledger_tampering_rejected(self):
        self.ledger["github_source_calls"][0]["path"] = "../fake"
        from gate_b import ProtocolError
        with self.assertRaisesRegex(ProtocolError, "claimed source/slot changed"):
            self.check()


if __name__ == "__main__":
    unittest.main()

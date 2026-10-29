"""RGP corpus integrity tests; only source verification, no model."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gate_b import ProtocolError, strict_json  # noqa: E402
from gate_b_rgp_audit import audit  # noqa: E402

PLAN = strict_json(ROOT / "benchmarks/gate_b/plan.json")


class CorpusAuditTest(unittest.TestCase):
    def test_missing_artifact_fails(self):
        corpus = {"artifacts": [{"repository": r["id"], "source_commit": r["commit"]}
                                for r in PLAN["repositories"]]}
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(ProtocolError, "missing RGP"):
            audit(PLAN, corpus, Path(td))

    def test_wrong_source_commit_fails(self):
        corpus = {"artifacts": [{"repository": r["id"], "source_commit": "f"*40}
                                for r in PLAN["repositories"]]}
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(ProtocolError, "wrong source commit"):
            audit(PLAN, corpus, Path(td))

    def test_rejects_unknown_path(self):
        plan = copy.deepcopy(PLAN)
        plan["tasks"][0]["reference_paths"] = ["missing/will/fail"]
        # validate_plan accepts safe unknown paths; audit enforces actual presence.
        corpus = {"artifacts": [{"repository": r["id"], "source_commit": r["commit"]}
                                for r in plan["repositories"]]}
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(ProtocolError, "missing RGP"):
            audit(plan, corpus, Path(td))


if __name__ == "__main__":
    unittest.main()

"""Offline contract tests for the Stage 1 pinned-corpus acceptance.

No network access, model API or external repositories required.
"""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("stage1_corpus", ROOT / "scripts/stage1_corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)
PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text())


class Stage1CorpusTests(unittest.TestCase):
    def test_all_frozen_task_paths_have_predeclared_anchors(self):
        self.assertEqual(len(corpus.ANCHORS), 12)
        for repo_id in ("rgp", "serde_json", "itsdangerous"):
            repo, tasks = corpus.tasks_for(PLAN, repo_id)
            self.assertEqual(repo["id"], repo_id)
            self.assertEqual(len(tasks), 4)
            for task in tasks:
                self.assertEqual(set(corpus.ANCHORS[task["id"]]), set(task["reference_paths"]))

    def test_rejects_new_task_without_anchor(self):
        plan = copy.deepcopy(PLAN)
        plan["tasks"][0]["id"] = "renamed-task"
        with self.assertRaisesRegex(ValueError, "anchor task ids"):
            corpus.tasks_for(plan, "rgp")

    def test_rejects_missing_reference_path_anchor(self):
        original = corpus.ANCHORS["rgp-transport-hash"]
        try:
            corpus.ANCHORS["rgp-transport-hash"] = {"src/main.rs": ("payload_sha256=",)}
            with self.assertRaisesRegex(ValueError, "anchor paths missing"):
                corpus.tasks_for(PLAN, "rgp")
        finally:
            corpus.ANCHORS["rgp-transport-hash"] = original

    def test_unknown_repository_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown repository"):
            corpus.tasks_for(PLAN, "unknown")

    def test_pinned_source_checkpoint_is_independent_of_old_transport(self):
        checkpoints = json.loads((ROOT / "benchmarks/gate_b/corpus_preparation.json").read_text())
        for item in checkpoints["artifacts"]:
            summary = {"repository": item["repository"],
                       "source_commit": item["source_commit"],
                       "included_files": item["included_files"],
                       "transport_version": "RGP/0.1.0"}
            self.assertEqual(corpus.match_checkpoint(checkpoints, summary), "SOURCE_EQUIVALENCE_ONLY")
            with self.assertRaisesRegex(ValueError, "source regression"):
                corpus.match_checkpoint(checkpoints, dict(summary, included_files=0))
            with self.assertRaisesRegex(ValueError, "unsupported transport"):
                corpus.match_checkpoint(checkpoints, dict(summary, transport_version="RGP/3.0"))

    def test_generator_failure_is_a_failure_not_success(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "invalid-time"
            with self.assertRaises(RuntimeError):
                corpus.measured(["/bin/false"], path)

    def test_measurement_parsing_requires_complete_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "receipt"
            with patch.object(corpus, "execute", side_effect=lambda args: path.write_text("12\n")):
                with self.assertRaisesRegex(ValueError, "two metrics"):
                    corpus.measured(["/bin/true"], path)


if __name__ == "__main__":
    unittest.main()

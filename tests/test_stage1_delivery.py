"""Offline Stage 1 delivery trust-boundary and frozen-source contract tests."""
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage1_delivery as delivery  # noqa: E402
from legacy_transport_fixture import make_fixture  # noqa: E402

PLAN = json.loads((ROOT / "benchmarks/gate_b/plan.json").read_text())
CHECKPOINT = json.loads((ROOT / "benchmarks/gate_b/corpus_preparation.json").read_text())


def zipped(items):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for name, content in items:
            archive.writestr(name, content)
    return out.getvalue()


class DeliveryTests(unittest.TestCase):
    def test_all_frozen_repositories_have_one_valid_checkpoint(self):
        for repo in PLAN["repositories"]:
            selected, row, tasks = delivery.pinned_expectation(PLAN, CHECKPOINT, repo["id"])
            self.assertEqual(selected["commit"], row["source_commit"])
            self.assertEqual(len(tasks), 4)

    def test_rejects_missing_or_duplicate_checkpoint(self):
        bad = copy.deepcopy(CHECKPOINT)
        bad["artifacts"].append(copy.deepcopy(bad["artifacts"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate repository checkpoint"):
            delivery.pinned_expectation(PLAN, bad, bad["artifacts"][0]["repository"])
        bad["artifacts"] = []
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            delivery.pinned_expectation(PLAN, bad, "rgp")

    def test_rejects_corrupt_checkpoint_or_changed_git_commit(self):
        bad = copy.deepcopy(CHECKPOINT)
        for row in bad["artifacts"]:
            if row["repository"] == "rgp":
                row["sha256"] = "not-a-sha"
        with self.assertRaisesRegex(ValueError, "invalid checkpoint digest"):
            delivery.pinned_expectation(PLAN, bad, "rgp")
        bad = copy.deepcopy(CHECKPOINT)
        for row in bad["artifacts"]:
            if row["repository"] == "rgp":
                row["source_commit"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "source revision"):
            delivery.pinned_expectation(PLAN, bad, "rgp")

    def test_actual_zip_bootstrap_and_negative_external_sha(self):
        # Independent synthetic RGP with a fake but explicit pinned
        # repository/commit/checkpoint. No network, external model or
        # requirements beyond the stdlib are involved.
        data, record = make_fixture(32 * 1024)
        plan = copy.deepcopy(PLAN)
        checkpoints = copy.deepcopy(CHECKPOINT)
        selected = next(r for r in plan["repositories"] if r["id"] == "rgp")
        selected["commit"] = "0" * 40
        row = next(r for r in checkpoints["artifacts"] if r["repository"] == "rgp")
        row.update({"source_commit": "0" * 40,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "rgp_bytes": len(data), "included_files": 1})
        source_path = "probe/chunk-0000.bin"
        tasks = [t for t in plan["tasks"] if t["repository"] == "rgp"]
        overrides = {}
        for task in tasks:
            task["reference_paths"] = [source_path]
            overrides[task["id"]] = {source_path: ("synthetic-predeclared-anchor",)}
        with patch.dict(delivery.ANCHORS, overrides), tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "remote.zip"
            output = root / "restored.rgp"
            source.write_bytes(zipped([("REPO_CONTEXT.rgp", data)]))
            result = delivery.verify_delivered(
                source, output, "rgp", plan, checkpoints)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["task_reference_sets"], 4)
            self.assertEqual(result["source_comparison"], "NOT_RUN")
            self.assertEqual(result["connector_acquisition"], "NOT_ATTESTED_BY_LOCAL_SCRIPT")
            self.assertEqual(result["gate_b"], "HOLD")
            self.assertEqual(output.read_bytes(), data)

            output.unlink()
            bad_checkpoint = copy.deepcopy(checkpoints)
            next(r for r in bad_checkpoint["artifacts"] if r["repository"] == "rgp")["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "HASH_MISMATCH"):
                delivery.verify_delivered(source, output, "rgp", plan, bad_checkpoint)
            self.assertFalse(output.exists())
            source.write_bytes(zipped([("REPO_CONTEXT.rgp", data),
                                       ("unexpected.sh", b"echo forged")]))
            with self.assertRaisesRegex(ValueError, "UNSUPPORTED_FORMAT"):
                delivery.verify_delivered(source, output, "rgp", plan, checkpoints)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

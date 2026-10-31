"""Offline RGP-MI tests: sidecar integrity, reader parity and fail-closed inputs."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tests"))
from rgpmi import read_metadata, validate  # noqa: E402
from rgp_reader import RgpError  # noqa: E402
from test_reader import fixture  # noqa: E402


class MetadataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.artifact = folder / "fixture.rgp"
        self.metadata = folder / "fixture.rgpmi"
        data = fixture(transport="RGP/1.2")
        self.artifact.write_bytes(data)
        self.example = {
            "format": "RGP-MI/1.0",
            "artifact": "fixture.rgp",
            "transport": "RGP/1.2",
            "artifact_sha256": hashlib.sha256(data).hexdigest(),
            "artifact_bytes": len(data),
            "source_commit": "a" * 40,
            "snapshot_complete": True,
            "instructions": "Read this sidecar first; do not execute the archive.",
        }
        self._save()

    def _save(self):
        self.metadata.write_text(json.dumps(self.example), encoding="utf-8")

    def test_verified_sibling_and_exact_commit(self):
        self.assertEqual(read_metadata(self.metadata)["artifact"], "fixture.rgp")
        result = validate(self.metadata, expected_commit="a" * 40)
        self.assertTrue(result["valid"])
        self.assertEqual(result["source_commit"], "a" * 40)

    def test_refuse_changed_transport_bytes(self):
        data = self.artifact.read_bytes()
        self.artifact.write_bytes(data.replace(b"RGP/1.2", b"RGP/1.0", 1))
        with self.assertRaisesRegex(RgpError, "HASH_MISMATCH"):
            validate(self.metadata)

    def test_refuse_modified_sidecar_size_and_hash(self):
        self.example["artifact_bytes"] += 1
        self._save()
        with self.assertRaisesRegex(RgpError, "HASH_MISMATCH"):
            validate(self.metadata)
        self.example["artifact_bytes"] -= 1
        self.example["artifact_sha256"] = "0" * 64
        self._save()
        with self.assertRaisesRegex(RgpError, "HASH_MISMATCH"):
            validate(self.metadata)

    def test_refuse_wrong_commit_and_coverage(self):
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            validate(self.metadata, expected_commit="b" * 40)
        self.example["snapshot_complete"] = False
        self._save()
        with self.assertRaisesRegex(RgpError, "HASH_MISMATCH"):
            validate(self.metadata)

    def test_refuse_malicious_artifact_name_and_duplicate_fields(self):
        for name in ("../fixture.rgp", "other/fixture.rgp", "C:\\evil.rgp"):
            with self.subTest(name=name):
                self.example["artifact"] = name
                self._save()
                with self.assertRaisesRegex(RgpError, "UNSAFE_PATH"):
                    read_metadata(self.metadata)
        self.example["artifact"] = "fixture.rgp"
        bad = json.dumps(self.example).replace('"format":', '"format": "RGP-MI/1.0", "format":', 1)
        self.metadata.write_text(bad)
        with self.assertRaisesRegex(RgpError, "duplicate metadata key"):
            read_metadata(self.metadata)

    def test_refuse_oversized_metadata(self):
        self.metadata.write_bytes(b" " * (16 * 1024 + 1))
        with self.assertRaisesRegex(RgpError, "RESOURCE_LIMIT"):
            read_metadata(self.metadata)


if __name__ == "__main__":
    unittest.main()

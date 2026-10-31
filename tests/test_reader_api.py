"""Reader contract for the only supported format, RGP/0.1.0."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError
from rgp_fixture import make_artifact, three_scope_artifact

class ReaderApiTests(unittest.TestCase):
    def setUp(self):
        self.reader = Reader(three_scope_artifact())

    def test_version_scope_and_guide(self):
        overview = self.reader.overview()
        self.assertEqual(overview["transport"], "RGP/0.1.0")
        self.assertIsNone(overview["source_commit"])
        self.assertTrue(overview["snapshot_complete"])
        self.assertEqual(overview["file_count"], 4)
        self.assertTrue(overview["embedded_instructions"])
        self.assertEqual(len(self.reader._blobs), 4)

    def test_read_tree_and_search(self):
        self.assertEqual(self.reader.read("src/a.py")["content"], "needle from src A\nneedle again\n")
        self.assertEqual(self.reader.tree(prefix="src/")["paths"], ["src/a.py", "src/sub/b.py"])
        self.assertEqual(len(self.reader.search("needle")["matches"]), 5)
        self.assertEqual(len(self.reader.search("needle", prefix="src/")["matches"]), 3)

    def test_paginated_cursors(self):
        a = self.reader.tree(limit=2)
        self.assertEqual(len(self.reader.tree(limit=2, cursor=a["next_cursor"])["paths"]), 2)
        with self.assertRaises(RgpError):
            self.reader.tree(prefix="tests/", cursor=a["next_cursor"])
        first = self.reader.search("needle", limit=2)
        second = self.reader.search("needle", limit=3, cursor=first["next_cursor"])
        self.assertEqual(len(first["matches"]) + len(second["matches"]), 5)

    def test_errors_and_scoped_coverage(self):
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            self.reader.read("missing")
        with self.assertRaises(RgpError):
            self.reader.search("needle", prefix="../")
        partial = Reader(make_artifact({"x.txt": b"abc\n"}, ignored=2,
                                       excluded=(("missing.bin", "non_text"),)))
        self.assertFalse(partial.overview()["snapshot_complete"])
        self.assertEqual(partial.overview()["intentionally_ignored_files"], 2)

    def test_reject_all_old_versions(self):
        source = three_scope_artifact()
        for version in ("RGP/1.0", "RGP/1.1", "RGP/1.2", "RGP/2.0", "RGP/2.1", "RGP/3.0"):
            with self.subTest(version=version), self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
                Reader(source.replace(b"RGP/0.1.0", version.encode(), 1))

    def test_truncation_and_external_trust_boundary(self):
        source = three_scope_artifact()
        with self.assertRaisesRegex(RgpError, "TRUNCATED_ARTIFACT"):
            Reader(source[:-8])
        changed = source.replace(b"needle from src A", b"needle from src B")
        self.assertIn("src B", Reader(changed).read("src/a.py")["content"])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            artifact = Path(folder) / "snapshot.rgp"
            artifact.write_bytes(three_scope_artifact())
            command = [sys.executable, str(ROOT / "python/rgp_reader.py"),
                       "--artifact", str(artifact), "search", "needle", "--prefix", "src/"]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(len(json.loads(result.stdout)["matches"]), 3)

if __name__ == "__main__":
    unittest.main()

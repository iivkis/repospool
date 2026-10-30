"""Independent byte-level golden contract for v1 RGP (no Rust or pip required)."""
import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError, decode_transport  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "v1"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


class GoldenContractTests(unittest.TestCase):
    def test_all_fixtures_have_pinned_length_and_sha256(self):
        for name, expected in EXPECTED["artifacts"].items():
            with self.subTest(name=name):
                artifact = (FIXTURES / name).read_bytes()
                self.assertEqual(len(artifact), expected["bytes"])
                self.assertEqual(hashlib.sha256(artifact).hexdigest(), expected["sha256"])

    def test_valid_mixed_snapshot_and_original_byte_fidelity(self):
        reader = Reader(FIXTURES / "valid-mixed.rgp")
        self.assertTrue(reader.validate()["valid"])
        self.assertEqual(reader.overview()["input_tree_digest"], EXPECTED["mixed_input_tree_digest"])
        self.assertEqual(reader.manifest["files"], EXPECTED["mixed_files"])
        self.assertEqual(reader.manifest["excluded"], EXPECTED["mixed_excluded"])
        self.assertFalse(reader.overview()["snapshot_complete"])
        self.assertEqual(reader.tree()["paths"], [entry["path"] for entry in EXPECTED["mixed_files"]])
        self.assertEqual(reader.read("README.md")["content"], "# sample\r\nUTF-8 and bytes are preserved.\n")
        self.assertEqual(reader.read("README.md", 1, 1)["content"], "# sample\r\n")
        self.assertEqual(reader.read("src/пример.py", 1, 1)["content"], "def ответ():\n")
        self.assertEqual(reader.read("scripts/link")["content"], "../README.md")
        self.assertEqual(reader.read("lfs/large.bin")["status"], "pointer_only")
        self.assertEqual(reader.search("return 42")["matches"][0]["path"], "src/пример.py")
        binary = reader.read("assets/raw.bin")
        self.assertEqual(binary["encoding"], "base64")
        self.assertEqual(base64.b64decode(binary["content"]), bytes(range(256)) + b"\0\xff")
        for entry in EXPECTED["mixed_files"]:
            self.assertEqual(hashlib.sha256(reader._blob(entry["path"])).hexdigest(), entry["sha256"])
        self.assertEqual(reader._files["README.md"]["blob"], reader._files["src/copy.md"]["blob"])
        self.assertEqual(len(reader._blobs), len({entry["sha256"] for entry in EXPECTED["mixed_files"]}))

    def test_valid_empty_snapshot(self):
        reader = Reader(FIXTURES / "valid-empty.rgp")
        self.assertEqual(reader.tree()["paths"], [])
        self.assertTrue(reader.overview()["snapshot_complete"])
        self.assertEqual(reader.overview()["input_tree_digest"], hashlib.sha256(b"").hexdigest())

    def test_negative_fixtures_fail_with_stable_error_codes(self):
        for name, code in EXPECTED["invalid"].items():
            with self.subTest(name=name), self.assertRaises(RgpError) as caught:
                Reader(FIXTURES / name)
            self.assertEqual(caught.exception.code, code)

    def test_payload_is_independently_decodable(self):
        for name in EXPECTED["valid"]:
            with self.subTest(name=name):
                payload = decode_transport((FIXTURES / name).read_bytes())
                self.assertTrue(payload.startswith(b"PK\x03\x04"))


if __name__ == "__main__":
    unittest.main()

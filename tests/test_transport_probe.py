"""Independent fixtures for connector tests must remain valid Reader inputs."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from transport_probe import generate, make_fixture, verify  # noqa: E402


class TransportProbeTest(unittest.TestCase):
    def test_deterministic_reader_compatible_fixture(self):
        first, expected = make_fixture(32 * 1024)
        second, other = make_fixture(32 * 1024)
        self.assertEqual(first, second)
        self.assertEqual(expected, other)
        self.assertLess(abs(len(first) - 32 * 1024), 32 * 1024 // 100)

    def test_generated_fixture_verifies_and_detects_truncation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            generate(root, [100 * 1024])
            verify(root, root / "expected.json")
            expected = json.loads((root / "expected.json").read_text())[0]
            fixture = root / expected["file"]
            fixture.write_bytes(fixture.read_bytes()[:-17])
            with self.assertRaisesRegex(ValueError, "TRANSPORT_MISMATCH"):
                verify(root, root / "expected.json")

    def test_single_byte_change_and_reordering_detected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            generate(root, [32 * 1024])
            expected = json.loads((root / "expected.json").read_text())[0]
            fixture = root / expected["file"]
            content = bytearray(fixture.read_bytes())
            content[200] = ord("A") if content[200] != ord("A") else ord("B")
            fixture.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "TRANSPORT_MISMATCH"):
                verify(root, root / "expected.json")


if __name__ == "__main__":
    unittest.main()

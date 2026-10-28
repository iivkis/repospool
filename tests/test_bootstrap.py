"""Security-focused tests of the stdlib-only Actions artifact bootstrap."""
import io
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rgp_bootstrap import bootstrap  # noqa: E402
from rgp_reader import RgpError  # noqa: E402
from transport_probe import make_fixture  # noqa: E402


def actions_zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, contents in entries:
            archive.writestr(name, contents)
    return output.getvalue()


class BootstrapTest(unittest.TestCase):
    def test_roundtrip_with_external_trust(self):
        data, record = make_fixture(32 * 1024)
        wrapper = actions_zip([
            (record["file"], data),
            ("expected.json", json.dumps([record]).encode()),
        ])
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source, output = folder / "remote.zip", folder / "restored.rgp"
            source.write_bytes(wrapper)
            result = bootstrap(source, output, record["transport_sha256"])
            self.assertEqual(output.read_bytes(), data)
            self.assertEqual(result["sha256"], record["transport_sha256"])
            self.assertTrue(result["valid"])

    def test_actions_corpus_provenance_roundtrip(self):
        from rgp_reader import Reader
        data, record = make_fixture(32 * 1024)
        reader = Reader(data)
        provenance = {
            "repository": "test-source",
            "source_commit": reader.manifest["source_commit"],
            "rgp_sha256": hashlib.sha256(data).hexdigest(),
            "rgp_bytes": len(data),
            "plan_sha256": "f" * 64,
            "reader_overview": reader.overview(),
            "transport_verified": False,
        }
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source, output = folder / "remote.zip", folder / "restored.rgp"
            source.write_bytes(actions_zip([
                ("REPO_CONTEXT.rgp", data),
                ("provenance.json", json.dumps(provenance).encode()),
            ]))
            result = bootstrap(source, output, provenance["rgp_sha256"])
            self.assertTrue(result["valid"])
            self.assertEqual(output.read_bytes(), data)

    def test_rejects_forged_corpus_provenance(self):
        from rgp_reader import Reader
        data, record = make_fixture(32 * 1024)
        reader = Reader(data)
        metadata = {
            "source_commit": reader.manifest["source_commit"],
            "rgp_sha256": hashlib.sha256(data).hexdigest(),
            "rgp_bytes": len(data),
            "plan_sha256": "f" * 64,
            "reader_overview": reader.overview(),
            "transport_verified": False,
        }
        for mutation in (
            {"rgp_sha256": "0" * 64},
            {"rgp_bytes": len(data) + 1},
            {"source_commit": "a" * 40},
            {"plan_sha256": "not-hex"},
            {"transport_verified": True},
            {"reader_overview": {"input_tree_digest": "f" * 64, "file_count": 1}},
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as name:
                bad = dict(metadata, **mutation)
                source = Path(name) / "remote.zip"
                output = Path(name) / "restored.rgp"
                source.write_bytes(actions_zip([
                    ("REPO_CONTEXT.rgp", data),
                    ("provenance.json", json.dumps(bad).encode()),
                ]))
                with self.assertRaises(RgpError):
                    bootstrap(source, output, metadata["rgp_sha256"])
                self.assertFalse(output.exists())

    def test_untrusted_hash_fails_without_output(self):
        data, record = make_fixture(32 * 1024)
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source, output = folder / "remote.zip", folder / "restored.rgp"
            source.write_bytes(actions_zip([(record["file"], data)]))
            with self.assertRaisesRegex(RgpError, "HASH_MISMATCH"):
                bootstrap(source, output, "0" * 64)
            self.assertFalse(output.exists())

    def test_rejects_traversal_and_duplicates(self):
        data, record = make_fixture(32 * 1024)
        for bad in [
            [("../payload.rgp", data)],
            [(record["file"], data), (record["file"], data)],
            [(record["file"], data), ("x.sh", b"echo wrong")],
        ]:
            with self.subTest(entries=[name for name, _ in bad]):
                with tempfile.TemporaryDirectory() as name:
                    source = Path(name) / "remote.zip"
                    source.write_bytes(actions_zip(bad))
                    with self.assertRaises(RgpError):
                        bootstrap(source, Path(name) / "restored.rgp")

    def test_rejects_bad_manifest_size(self):
        data, record = make_fixture(32 * 1024)
        record["transport_bytes"] += 1
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "remote.zip"
            source.write_bytes(actions_zip([
                (record["file"], data), ("expected.json", json.dumps([record]).encode()),
            ]))
            with self.assertRaisesRegex(RgpError, "HASH_MISMATCH"):
                bootstrap(source, Path(name) / "restored.rgp")


if __name__ == "__main__":
    unittest.main()

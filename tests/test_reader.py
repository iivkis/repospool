"""Reader tests use independently constructed ZIP fixtures (no Rust or pip needed)."""
import base64
import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from rgp_reader import Reader, RgpError, decode_transport  # noqa: E402


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture(path="src/example.py", content=b"hello\nworld\n", extra=None):
    blob_name = "files/" + sha(content)
    tree = hashlib.sha256(path.encode() + b"\x00100644\x00" + bytes.fromhex(sha(content))).hexdigest()
    manifest = {
        "schema_version": "1.0", "source_commit": "a" * 40, "input_tree_digest": tree,
        "files": [{"path": path, "mode": "100644", "kind": "file", "size": len(content), "sha256": sha(content), "blob": blob_name, "status": "included"}],
        "excluded": [], "index_coverage": "not_indexed", "graph_coverage": "not_indexed",
    }
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(blob_name, content)
        if extra:
            archive.writestr(extra[0], extra[1])
    payload = mem.getvalue()
    b64 = base64.b64encode(payload).decode()
    data = (
        f"RGP/1.0\npayload_length={len(payload)}\npayload_sha256={sha(payload)}\n"
        "base64_line_width=76\n--\n" +
        "".join(b64[i:i + 76] + "\n" for i in range(0, len(b64), 76)) + "END RGP\n"
    )
    return data.encode()


class ReaderTest(unittest.TestCase):
    def test_read_and_search(self):
        r = Reader(fixture())
        self.assertTrue(r.validate()["valid"])
        self.assertEqual(r.tree()["paths"], ["src/example.py"])
        self.assertEqual(r.read("src/example.py", 2, 2)["content"], "world\n")
        self.assertEqual(r.search("world")["matches"][0]["line"], 2)
        self.assertTrue(r.overview()["snapshot_complete"])

    def test_read_bytes_preserves_exact_git_blob_bytes(self):
        original = bytes([255, 0, 13, 10]) + b"raw" + bytes([10])
        r = Reader(fixture(content=original))
        self.assertEqual(r.read_bytes("src/example.py"), original)
        self.assertEqual(r.read_bytes("src/example.py"), original)
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            r.read_bytes("missing.txt")
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            r.read_bytes(None)

    def test_truncation(self):
        with self.assertRaisesRegex(RgpError, "TRUNCATED_ARTIFACT"):
            decode_transport(fixture()[:-12])

    def test_payload_tamper(self):
        data = fixture().replace(b"payload_sha256=", b"payload_sha256=1", 1)
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            Reader(data)

    def test_zip_slip(self):
        with self.assertRaisesRegex(RgpError, "UNSAFE_PATH"):
            Reader(fixture(extra=("../oops", b"bad")))

    def test_unreferenced_content(self):
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            Reader(fixture(extra=("files/" + sha(b"extra"), b"extra")))

    def test_binary_is_base64(self):
        self.assertEqual(Reader(fixture(content=b"\xff\x00")).read("src/example.py")["encoding"], "base64")

    def test_bad_line_range(self):
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            Reader(fixture()).read("src/example.py", 5, 9)


if __name__ == "__main__":
    unittest.main()

"""Readable RGP/2.0 independent test vectors (no ZIP, Base64 or Rust dependency)."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError  # noqa: E402
from rgpmi import validate as validate_metadata  # noqa: E402


def digest(b):
    return hashlib.sha256(b).hexdigest()


def build_plain(files=None, excluded=None):
    if files is None:
        files = {"src/example.py": b'print("END RGP\\n--\\nFILE\\t")\r\n',
                 "src/unicode.txt": "Привет, мир!\n".encode("utf-8")}
    entries = []
    for path, raw in sorted(files.items()):
        entries.append({"path": path, "mode": "100644", "kind": "file",
                        "size": len(raw), "sha256": digest(raw),
                        "blob": f"files/{digest(raw)}", "status": "included"})
    th = hashlib.sha256()
    for entry in entries:
        th.update(entry["path"].encode("utf-8") + b"\0" +
                  entry["mode"].encode() + b"\0" + bytes.fromhex(entry["sha256"]))
    manifest = {"schema_version": "1.0", "source_commit": "a" * 40,
                "input_tree_digest": th.hexdigest(), "files": entries,
                "excluded": excluded or [], "index_coverage": "not_indexed",
                "graph_coverage": "not_indexed"}
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    raw_rgp = (f"RGP/2.0\nmanifest_length={len(manifest_bytes)}\n"
               f"manifest_sha256={digest(manifest_bytes)}\n--\n").encode()
    raw_rgp += manifest_bytes + b"\n"
    for entry in entries:
        raw_rgp += (f"FILE\t{json.dumps(entry['path'], ensure_ascii=False)}\t"
                    f"{entry['size']}\t{entry['sha256']}\n").encode()
        raw_rgp += files[entry["path"]] + b"\n"
    return raw_rgp + b"END RGP\n"


class PlainReaderTest(unittest.TestCase):
    def test_exact_readable_original_content_and_search(self):
        raw = build_plain()
        self.assertTrue(raw.startswith(b"RGP/2.0\n"))
        self.assertNotIn(b"base64_line_width", raw)
        self.assertNotIn(b"PK\x03\x04", raw)
        reader = Reader(raw)
        self.assertTrue(reader.validate()["valid"])
        self.assertTrue(reader.overview()["snapshot_complete"])
        self.assertEqual(reader.tree()["paths"], ["src/example.py", "src/unicode.txt"])
        self.assertEqual(reader.read_bytes("src/unicode.txt"), "Привет, мир!\n".encode())
        self.assertEqual(reader.search("Привет")["matches"][0]["line"], 1)
        self.assertEqual(reader.read_bytes("src/example.py"),
                         b'print("END RGP\\n--\\nFILE\\t")\r\n')

    def test_delimiter_text_cannot_escape_source_section(self):
        raw = build_plain({"src/markers.txt": b"END RGP\n--\nFILE\t\"p\"\t0\tX\n"})
        self.assertEqual(Reader(raw).read_bytes("src/markers.txt"),
                         b"END RGP\n--\nFILE\t\"p\"\t0\tX\n")

    def test_tampering_and_truncation_fail(self):
        raw = build_plain()
        for broken, error in (
            (raw.replace("Привет".encode("utf-8"), "Привот".encode("utf-8"), 1), "HASH_MISMATCH"),
            (raw[:-7], "TRUNCATED_ARTIFACT"),
            (raw.replace(b"manifest_sha256=", b"manifest_sha256=0", 1), "UNSUPPORTED_FORMAT"),
            (raw.replace(b"FILE\t", b"F1LE\t", 1), "UNSUPPORTED_FORMAT"),
            (raw.replace(b"RGP/2.0", b"RGP/9.0", 1), "UNSUPPORTED_FORMAT"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(RgpError, error):
                    Reader(broken)

    def test_rgpignore_explicitly_excludes_without_reducing_scope_completeness(self):
        excluded = [{"path": "benchmarks/old.md", "reason": "rgpignore"}]
        raw = build_plain(
            {".rgpignore": b"/benchmarks/\n", "src/code.py": b"answer = 42\n"},
            excluded=excluded,
        )
        reader = Reader(raw)
        report = reader.overview()
        self.assertTrue(reader.validate()["valid"])
        self.assertTrue(report["snapshot_complete"])
        self.assertEqual(report["intentionally_ignored_files"], 1)
        self.assertEqual(report["excluded_by_reason"]["rgpignore"], 1)
        self.assertEqual(report["excluded_files"], 0)
        self.assertEqual(reader.read_bytes(".rgpignore"), b"/benchmarks/\n")
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            reader.read_bytes("benchmarks/old.md")

    def test_invalid_utf8_fails(self):
        raw = build_plain()
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            Reader(raw + b"\xff")

    def test_sidecar_checks_full_plain_artifact(self):
        raw = build_plain()
        with tempfile.TemporaryDirectory() as folder:
            rgp = Path(folder) / "test.rgp"
            rgp.write_bytes(raw)
            sidecar = rgp.with_suffix(".rgpmi")
            sidecar.write_text(json.dumps({
                "format": "RGP-MI/1.0", "artifact": "test.rgp",
                "transport": "RGP/2.0", "artifact_sha256": digest(raw),
                "artifact_bytes": len(raw), "source_commit": "a" * 40,
                "snapshot_complete": True, "instructions": "Read selected sources only."
            }))
            self.assertTrue(validate_metadata(sidecar, expected_commit="a" * 40)["valid"])


if __name__ == "__main__":
    unittest.main()

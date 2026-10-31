"""RGP/3.0 independent hash-free, token-lean reader vectors."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from rgp_reader import Reader, RgpError  # noqa: E402


def artifact(files=None, ignored=2, excluded=None):
    if files is None:
        files = {
            "src/example.py": b'print("END RGP\\nFILE\\tgotcha")\r\n',
            "src/unicode.txt": "Привет!\n".encode("utf-8"),
        }
    excluded = excluded or []
    guide = b"Read only relevant source and verify provenance from trusted Git."
    index = "".join(f"F\t{path}\t{len(raw)}\n" for path, raw in sorted(files.items()))
    if ignored:
        index += f"I\t{ignored}\n"
    index += "S\t1\n"
    index += "".join(f"X\t{name}\t{reason}\n" for name, reason in excluded)
    result = (f"RGP/3.0\nguide_bytes={len(guide)}\nindex_bytes={len(index.encode())}\n--\n"
              ).encode() + guide + b"\n" + index.encode() + b"\n"
    for path, raw in sorted(files.items()):
        result += f"FILE\t{path}\n".encode() + raw + b"\n"
    return result + b"END RGP\n"


class LeanTransportTest(unittest.TestCase):
    def test_no_embedded_hashes_and_original_content(self):
        raw = artifact()
        self.assertTrue(raw.startswith(b"RGP/3.0\n"))
        for token in (b"sha256", b"source_commit", b"manifest_length", b"input_tree_digest",
                      b"payload_sha256", b"base64_line_width", b'"blob"'):
            self.assertNotIn(token, raw)
        self.assertNotIn(b"PK\x03\x04", raw)
        reader = Reader(raw)
        overview = reader.overview()
        self.assertIsNone(overview["source_commit"])
        self.assertEqual(overview["transport"], "RGP/3.0")
        self.assertEqual(overview["intentionally_ignored_files"], 2)
        self.assertEqual(overview["self_artifact_excluded"], 1)
        self.assertTrue(overview["snapshot_complete"])
        self.assertTrue(reader.validate()["valid"])
        self.assertEqual(reader.read_bytes("src/example.py"),
                         b'print("END RGP\\nFILE\\tgotcha")\r\n')
        self.assertEqual(reader.search("Привет")["matches"][0]["line"], 1)
        self.assertEqual(reader.tree()["paths"], ["src/example.py", "src/unicode.txt"])

    def test_nonignored_exclusions_make_partial(self):
        r = Reader(artifact(excluded=[("sensitive/.env", "sensitive_path")]))
        self.assertFalse(r.overview()["snapshot_complete"])
        self.assertEqual(r.overview()["excluded_files"], 1)

    def test_bounds_and_delimiters_reject_corruption(self):
        raw = artifact()
        for changed, code in (
            (raw[:-8], "TRUNCATED_ARTIFACT"),
            (raw.replace(b"index_bytes=", b"index_bytes=999999999", 1), "RESOURCE_LIMIT"),
            (raw.replace(b"FILE\tsrc/example.py", b"F1LE\tsrc/example.py", 1),
             "UNSUPPORTED_FORMAT"),
            (raw.replace(b"RGP/3.0", b"RGP/9.0", 1), "UNSUPPORTED_FORMAT"),
            (raw + b"\xff", "UNSUPPORTED_FORMAT"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(RgpError, code):
                    Reader(changed)

    def test_equal_length_tamper_is_not_authenticated_without_external_git(self):
        raw = artifact({"file.py": b"answer = 42\n"}, ignored=0)
        changed = raw.replace(b"answer = 42", b"answer = 43")
        self.assertEqual(Reader(changed).read_bytes("file.py"), b"answer = 43\n")
        # This is an explicit security trade-off. The full Git blob hash
        # MUST be verified independently before treating RGP as trusted.


if __name__ == "__main__":
    unittest.main()

"""Negative manifest/ZIP tests: trusted Reader must fail closed without pip."""
import base64
import hashlib
import io
import json
import stat
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from rgp_reader import Reader, RgpError, decode_transport  # noqa: E402


def rgp_from_zip(zip_bytes):
    digest = hashlib.sha256(zip_bytes).hexdigest()
    b64 = base64.b64encode(zip_bytes)
    body = b"".join(b64[i:i+76] + b"\n" for i in range(0, len(b64), 76))
    return (
        f"RGP/1.0\npayload_length={len(zip_bytes)}\npayload_sha256={digest}\n"
        "base64_line_width=76\n--\n"
    ).encode() + body + b"END RGP\n"


def fake_artifact(mutate_manifest=None, symlink=False, duplicate_json=False):
    content = b"alpha\nbeta\nbeta\n"
    digest = hashlib.sha256(content).hexdigest()
    path = "src/file.txt"
    tree_hash = hashlib.sha256(path.encode() + b"\0" + b"100644\0" + bytes.fromhex(digest)).hexdigest()
    manifest = {
        "schema_version": "1.0", "source_commit": "a" * 40,
        "input_tree_digest": tree_hash,
        "files": [{"path": path, "mode": "100644", "kind": "file", "size": len(content),
                   "sha256": digest, "blob": "files/" + digest, "status": "included"}],
        "excluded": [], "index_coverage": "not_indexed", "graph_coverage": "not_indexed",
    }
    if mutate_manifest:
        mutate_manifest(manifest)
    serialized = json.dumps(manifest, separators=(",", ":"))
    if duplicate_json:
        serialized = serialized.replace('"schema_version":"1.0"', '"schema_version":"1.0","schema_version":"1.0"')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", serialized)
        info = zipfile.ZipInfo("files/" + digest)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = ((stat.S_IFLNK if symlink else stat.S_IFREG) | 0o644) << 16
        archive.writestr(info, content)
    return rgp_from_zip(out.getvalue())


class ManifestContractTest(unittest.TestCase):
    def test_valid_artifact_and_paged_search(self):
        reader = Reader(fake_artifact())
        self.assertTrue(reader.validate()["valid"])
        first = reader.search("beta", limit=1)
        self.assertEqual(first["matches"][0]["line"], 2)
        self.assertIsInstance(first["next_cursor"], str)
        second = reader.search("beta", limit=1, cursor=first["next_cursor"])
        self.assertEqual(second["matches"][0]["line"], 3)
        self.assertIsNone(second["next_cursor"])

    def test_invalid_commit_is_explicit_error(self):
        for commit in [None, "not-a-commit", "z" * 40]:
            with self.subTest(commit=commit), self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
                Reader(fake_artifact(lambda m: m.update(source_commit=commit)))

    def test_invalid_or_missing_coverage_is_explicit_error(self):
        for field in ["index_coverage", "graph_coverage"]:
            with self.subTest(field=field), self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
                Reader(fake_artifact(lambda m: m.pop(field)))

    def test_invalid_exclusion_reason_is_explicit_error(self):
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            Reader(fake_artifact(lambda m: m["excluded"].append({"path": ".env", "reason": None})))

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            Reader(fake_artifact(duplicate_json=True))

    def test_zip_symlink_rejected(self):
        with self.assertRaisesRegex(RgpError, "UNSAFE_PATH"):
            Reader(fake_artifact(symlink=True))

    def test_control_character_rejected(self):
        with self.assertRaisesRegex(RgpError, "UNSAFE_PATH"):
            Reader(fake_artifact(lambda m: m["excluded"].append({"path": "bad\u0085name", "reason": "size_limit"})))

    def test_bad_reader_api_arguments_rejected(self):
        r = Reader(fake_artifact())
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            r.read("src/file.txt", start_line="1")
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            r.search("beta", paths=[17])

    def test_unpaired_unicode_in_manifest_paths_fails_closed(self):
        # json.dumps intentionally encodes these as valid ASCII escape
        # sequences. The JSON is parseable, but paths are not UTF-8.
        bad = "src/" + chr(0xD800) + ".py"
        for mutate in (
            lambda m: m["files"][0].update(path=bad),
            lambda m: m["excluded"].append(
                {"path": bad, "reason": "size_limit"}),
        ):
            with self.subTest(mutate=mutate), self.assertRaisesRegex(
                    RgpError, "UNSAFE_PATH"):
                Reader(fake_artifact(mutate))

    def test_invalid_unicode_reader_queries_are_recoverable(self):
        reader = Reader(fake_artifact())
        invalid = "src/" + chr(0xD800)
        for query in (invalid, "../bad", "/etc/passwd"):
            with self.subTest(query=query), self.assertRaisesRegex(
                    RgpError, "UNSUPPORTED_FORMAT"):
                reader.tree(prefix=query)
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            reader.search(chr(0xD800))
        # An invalid agent-generated query must not poison later reads.
        self.assertEqual(reader.read("src/file.txt")["content"],
                         "alpha\nbeta\nbeta\n")

    def test_canonical_base64_newline(self):
        original = fake_artifact()
        tampered = original.replace(b"\nEND RGP\n", b"\n\nEND RGP\n")
        with self.assertRaisesRegex(RgpError, "TRUNCATED_ARTIFACT"):
            Reader(tampered)


if __name__ == "__main__":
    unittest.main()

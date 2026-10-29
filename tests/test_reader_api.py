"""MVP-A Reader bounds, pagination, completeness and cursor integrity."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError, MAX_RESULT  # noqa: E402
from test_manifest_contract import fake_artifact, rgp_from_zip  # noqa: E402

MIXED = ROOT / "tests" / "fixtures" / "v1" / "valid-mixed.rgp"


def three_scope_artifact():
    """Independent ZIP/transport fixture with source and test directories."""
    files = {
        "src/a.py": b"needle from src A\nneedle again\n",
        "src/sub/b.py": b"ignore\nneedle from sub B\n",
        "src2/unrelated.py": b"needle from src2\n",
        "tests/test_a.py": b"needle from tests\n",
    }
    records = []
    input_digest = hashlib.sha256()
    blobs = {}
    for path, data in sorted(files.items()):
        sha = hashlib.sha256(data).hexdigest()
        blob = "files/" + sha
        records.append({"path": path, "mode": "100644", "kind": "file",
                        "size": len(data), "sha256": sha, "blob": blob,
                        "status": "included"})
        input_digest.update(path.encode() + b"\0" + b"100644" + b"\0"
                            + bytes.fromhex(sha))
        blobs[blob] = data
    manifest = {"schema_version": "1.0", "source_commit": "a" * 40,
                "input_tree_digest": input_digest.hexdigest(),
                "files": records, "excluded": [],
                "index_coverage": "not_indexed", "graph_coverage": "not_indexed"}
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in sorted(blobs.items()):
            archive.writestr(name, data)
    return rgp_from_zip(mem.getvalue())


class ReaderApiTests(unittest.TestCase):
    def test_tree_cursor_is_stable_and_bound_to_prefix(self):
        r = Reader(MIXED)
        first = r.tree(limit=2)
        self.assertEqual(len(first["paths"]), 2)
        self.assertIsInstance(first["next_cursor"], str)
        second = r.tree(limit=2, cursor=first["next_cursor"])
        self.assertEqual(first["paths"] + second["paths"], r.tree(limit=4)["paths"])
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            r.tree(prefix="src/", cursor=first["next_cursor"])

    def test_search_keyset_cursor_scope_and_exhaustion(self):
        r = Reader(fake_artifact())
        first = r.search("beta", limit=1)
        self.assertEqual([x["line"] for x in first["matches"]], [2])
        token = first["next_cursor"]
        self.assertTrue(token)
        last = r.search("beta", limit=1, cursor=token)
        self.assertEqual([x["line"] for x in last["matches"]], [3])
        self.assertIsNone(last["next_cursor"])
        for kwargs in [{"query": "alpha"}, {"query": "beta", "paths": ["src/file.txt"]}]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
                r.search(cursor=token, **kwargs)

    def test_search_prefix_scopes_subtrees_not_siblings(self):
        r = Reader(three_scope_artifact())
        scoped = r.search("needle", prefix="src/")
        self.assertEqual([(m["path"], m["line"]) for m in scoped["matches"]],
                         [("src/a.py", 1), ("src/a.py", 2),
                          ("src/sub/b.py", 2)])
        self.assertEqual(len(r.search("needle", prefix="tests/")["matches"]), 1)
        self.assertEqual(r.search("needle", prefix="missing/")["matches"], [])
        # This is a *lexical* prefix: callers need a trailing slash to
        # express a directory boundary.
        self.assertEqual(len(r.search("needle", prefix="src")["matches"]), 4)
        self.assertEqual(len(r.search("needle")["matches"]), 5)

    def test_search_prefix_cursor_remains_bound_to_exact_scope(self):
        r = Reader(three_scope_artifact())
        first = r.search("needle", prefix="src/", limit=1)
        token = first["next_cursor"]
        self.assertIsInstance(token, str)
        second = r.search("needle", prefix="src/", limit=1, cursor=token)
        third = r.search("needle", prefix="src/", limit=1, cursor=second["next_cursor"])
        self.assertEqual([(x["path"], x["line"]) for part in
                          (first, second, third) for x in part["matches"]],
                         [("src/a.py", 1), ("src/a.py", 2), ("src/sub/b.py", 2)])
        self.assertIsNone(third["next_cursor"])
        for bad in ("src", "src/a", "src/sub/", "", "tests/"):
            with self.subTest(prefix=bad), self.assertRaisesRegex(
                    RgpError, "cursor does not match"):
                r.search("needle", prefix=bad, cursor=token)
        # Preserve cursor compatibility for the original unscoped API.
        old_token = r.search("needle", limit=1)["next_cursor"]
        self.assertEqual(r.search("needle", cursor=old_token)["matches"][0]["line"], 2)
        with self.assertRaisesRegex(RgpError, "cursor does not match"):
            r.search("needle", prefix="src/", cursor=old_token)

    def test_search_prefix_and_exact_paths_must_agree(self):
        r = Reader(three_scope_artifact())
        response = r.search("needle", prefix="src/", paths=["src/sub/b.py"])
        self.assertEqual([m["path"] for m in response["matches"]], ["src/sub/b.py"])
        with self.assertRaisesRegex(RgpError, "outside prefix"):
            r.search("needle", prefix="src/", paths=["tests/test_a.py"])
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            r.search("needle", prefix="src/", paths=["src/missing.py"])

    def test_search_prefix_rejects_unsafe_or_invalid_scope(self):
        r = Reader(three_scope_artifact())
        for bad in (None, 1, True, "/src", "../", "src/../",
                    "src//", "src\\file", "src/./", "src/\n",
                    "\\ud800", "あ" * 1500):
            with self.subTest(prefix=str(bad)[:18]), self.assertRaisesRegex(
                    RgpError, "invalid search prefix"):
                r.search("needle", prefix=bad)
        self.assertEqual(r.search("needle", prefix="src/")["snapshot_status"], "available")

    def test_search_prefix_cli_includes_only_requested_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "source.rgp"
            artifact.write_bytes(three_scope_artifact())
            cmd = [sys.executable, str(ROOT / "python/rgp_reader.py"),
                   "--artifact", str(artifact), "search", "needle",
                   "--prefix", "src/", "--limit", "10"]
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            obj = json.loads(result.stdout)
            self.assertEqual(len(obj["matches"]), 3)
            self.assertTrue(all(x["path"].startswith("src/") for x in obj["matches"]))
            rejected = subprocess.run(cmd[:-4] + ["--prefix", "../"],
                                      capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("UNSUPPORTED_FORMAT", rejected.stderr)

    def test_cursor_rejected_on_other_snapshot(self):
        r = Reader(fake_artifact())
        token = r.search("beta", limit=1)["next_cursor"]
        other = Reader(fake_artifact(lambda m: m["excluded"].append({"path": "unrelated", "reason": "size_limit"})))
        # Input digest intentionally excludes excluded paths, so this is the SAME
        # content snapshot: cursor reuse is allowed, but partial status is visible.
        self.assertEqual(other.search("beta", limit=1, cursor=token)["matches"][0]["line"], 3)
        self.assertEqual(other.search("beta")["snapshot_status"], "partial")
        # A different source blob must invalidate the cursor.
        changed = Reader(fake_artifact(lambda m: m.update(source_commit="b" * 40)))
        # source_commit is provenance; cursor is tied to the tree, not Git commit.
        self.assertEqual(changed.search("beta", limit=1, cursor=token)["matches"][0]["line"], 3)
        from test_reader import fixture
        another_tree = Reader(fixture(path="src/file.txt", content=b"alpha\nbeta\nbeta\nnew line\n"))
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            another_tree.search("beta", limit=1, cursor=token)

    def test_invalid_cursor_and_missing_scoped_path(self):
        r = Reader(fake_artifact())
        for cursor in (17, "x", "!", "A" * 2000):
            with self.subTest(cursor=str(cursor)[:10]), self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
                r.search("beta", cursor=cursor)
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            r.search("alpha", paths=["unavailable.txt"])
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            r.read(["src/file.txt"])

    def test_binary_base64_limit_is_encoded_bytes(self):
        # Incompressible data bypasses ZIP ratio guard and tests the actual output bound.
        binary = b"\xff" + os.urandom((MAX_RESULT * 3 // 4) + 1)
        from test_reader import fixture
        r = Reader(fixture(content=binary))
        with self.assertRaisesRegex(RgpError, "RESOURCE_LIMIT"):
            r.read("src/example.py")

    def test_search_excerpt_includes_match_far_into_long_line(self):
        from test_reader import fixture
        source = ("prefix-" * 160 + "MATCH_HERE" + "-suffix" * 100 + "\n").encode()
        r = Reader(fixture(content=source))
        found = r.search("MATCH_HERE")["matches"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["line"], 1)
        self.assertIn("MATCH_HERE", found[0]["excerpt"])
        self.assertLessEqual(len(found[0]["excerpt"].encode("utf-8")), 512)
        self.assertNotEqual(found[0]["excerpt"], source[:512].decode("utf-8"))

    def test_search_excerpt_preserves_multibyte_match_across_window(self):
        from test_reader import fixture
        query = "совпадение🌿"
        source = ("🌲" * 256 + query + "🌺" * 256 + "\n").encode("utf-8")
        r = Reader(fixture(content=source))
        first = r.search(query)["matches"][0]["excerpt"]
        self.assertIn(query, first)
        self.assertLessEqual(len(first.encode("utf-8")), 512)
        # The result is valid UTF-8 and never leaks a partial code point.
        self.assertEqual(first.encode("utf-8").decode("utf-8"), first)

    def test_search_excerpt_handles_query_longer_than_excerpt(self):
        from test_reader import fixture
        query = "Ж" * 300  # 600 UTF-8 bytes; full query cannot fit in excerpt.
        r = Reader(fixture(content=("a" * 1000 + query + "b" * 50 + "\n").encode()))
        found = r.search(query)["matches"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["line"], 1)
        self.assertTrue(found[0]["excerpt"].startswith("Ж"))
        self.assertLessEqual(len(found[0]["excerpt"].encode("utf-8")), 512)

    def test_search_excerpt_and_cursor_stay_consistent_for_long_lines(self):
        from test_reader import fixture
        line = "before" * 150 + "needle" + "after" * 150
        r = Reader(fixture(content=(line + "\n" + line + "\n").encode()))
        first = r.search("needle", limit=1)
        self.assertIn("needle", first["matches"][0]["excerpt"])
        self.assertTrue(first["next_cursor"])
        second = r.search("needle", limit=1, cursor=first["next_cursor"])
        self.assertEqual([x["line"] for x in second["matches"]], [2])
        self.assertIn("needle", second["matches"][0]["excerpt"])
        self.assertIsNone(second["next_cursor"])

    def test_bounded_utf8_search_excerpt(self):
        from test_reader import fixture
        # Non-ASCII input: excerpt cap must be encoded bytes, not code points.
        r = Reader(fixture(content=("🌲" * 400 + "needle\n").encode()))
        excerpt = r.search("needle")["matches"][0]["excerpt"]
        self.assertLessEqual(len(excerpt.encode("utf-8")), 512)

    def test_manifest_limit_enforced_before_decompression(self):
        with patch("rgp_reader.MAX_MANIFEST", 32):
            with self.assertRaisesRegex(RgpError, "RESOURCE_LIMIT"):
                Reader(fake_artifact())

    def test_snapshot_coverage_reports_real_gaps(self):
        r = Reader(MIXED)
        summary = r.coverage()
        self.assertEqual(summary["scope"], "snapshot")
        self.assertEqual(summary["status"], "partial")
        self.assertGreater(summary["pointer_only_files"], 0)
        self.assertGreater(summary["excluded_files"], 0)
        self.assertFalse(summary["snapshot_complete"])
        self.assertEqual(r.coverage("index")["status"], "not_indexed")
        self.assertEqual(r.coverage("graph")["status"], "not_indexed")
        self.assertEqual(r.coverage("investigation")["status"], "unknown")

    def test_self_artifact_is_not_partial_snapshot(self):
        r = Reader(fake_artifact(lambda m: m["excluded"].append({"path": "REPO_CONTEXT.rgp", "reason": "self_artifact"})))
        status = r.coverage("snapshot")
        self.assertTrue(status["snapshot_complete"])
        self.assertEqual(status["status"], "available")
        self.assertEqual(status["self_artifact_excluded"], 1)
        self.assertEqual(status["excluded_files"], 0)


if __name__ == "__main__":
    unittest.main()

"""MVP-A Reader bounds, pagination, completeness and cursor integrity."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError, MAX_RESULT  # noqa: E402
from test_manifest_contract import fake_artifact  # noqa: E402

MIXED = ROOT / "tests" / "fixtures" / "v1" / "valid-mixed.rgp"


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

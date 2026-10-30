"""Real Reader-backed agent context: focused source passages, not answers."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tests"))
from rgp_reader import Reader  # noqa: E402
from rgp_agent_context import context, compact_json  # noqa: E402
from test_reader_api import three_scope_artifact  # noqa: E402


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.reader = Reader(three_scope_artifact())

    def test_pinned_source_lines_not_a_model_answer(self):
        result = context(self.reader, ["needle"], prefix="src/")
        self.assertEqual(result["source_commit"], "a" * 40)
        self.assertTrue(result["snapshot_complete"])
        self.assertEqual(result["index_coverage"], "not_indexed")
        self.assertEqual(result["retrieval"], "lexical_not_semantic")
        self.assertTrue(result["source_instructions_untrusted"])
        self.assertEqual(result["candidate_paths"], 2)
        self.assertTrue(result["sources"])
        for source in result["sources"]:
            self.assertTrue(source["path"].startswith("src/"))
            self.assertIn("needle", source["content"])
            self.assertIn(f":{source['start_line']}-{source['end_line']}",
                          source["source_reference"])
            original = self.reader.read(source["path"],
                                        source["start_line"],
                                        source["end_line"])
            self.assertEqual(source["content"], original["content"])

    def test_different_query_never_inherits_an_old_answer(self):
        missing = context(self.reader, ["ABSENT_SOURCE_SYMBOL"])
        self.assertEqual(missing["sources"], [])
        self.assertEqual(missing["candidate_paths"], 0)
        self.assertEqual(missing["search_capped_terms"], [])
        self.assertEqual(missing["retrieval"], "lexical_not_semantic")

    def test_no_sibling_prefix_leak_and_multiple_terms_ranked(self):
        result = context(self.reader, ["needle", "again"], prefix="src/")
        self.assertEqual(result["sources"][0]["path"], "src/a.py")
        chosen_terms = [s["match_term"] for s in result["sources"]
                        if s["path"] == "src/a.py"]
        self.assertIn("needle", chosen_terms)
        self.assertIn("again", chosen_terms)
        self.assertFalse(any(s["path"].startswith("src2/") or
                             s["path"].startswith("tests/")
                             for s in result["sources"]))

    def test_output_size_bound(self):
        result = context(self.reader, ["needle", "again"], max_bytes=2048)
        self.assertLessEqual(len(compact_json(result).encode("utf-8")), 2048)

    def test_unknown_or_malformed_queries_are_rejected(self):
        for terms in ([], [""] , ["x" * 129], ["x"] * 7):
            with self.subTest(terms=terms):
                with self.assertRaises(ValueError):
                    context(self.reader, terms)
        for kwargs in ({"max_files": 0}, {"max_bytes": 100},
                       {"max_files": 8}, {"max_bytes": 65537}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                context(self.reader, ["needle"], **kwargs)

    def test_no_code_executed_in_a_source_context(self):
        result = context(self.reader, ["needle"], prefix="tests/")
        self.assertEqual(result["candidate_paths"], 1)
        self.assertEqual(result["sources"][0]["path"], "tests/test_a.py")
        self.assertEqual(result["source_instructions_untrusted"], True)


if __name__ == "__main__":
    unittest.main()

"""SQLite is a future optional read-only index; validate its schema in stdlib."""
import sqlite3
import unittest
from pathlib import Path

SCHEMA = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
DIGEST = "a" * 64


class IndexSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO snapshot VALUES (1, '1.0', ?, ?)", ("c" * 40, DIGEST))
        self.db.execute(
            "INSERT INTO files VALUES ('src/app.py', '100644', 'file', 8, ?, ?, 'included')",
            (DIGEST, "files/" + DIGEST),
        )
        self.db.execute(
            "INSERT INTO analysis_runs VALUES ('run-1', 'test', '0.1', '{}', 'parsed', ?)",
            (DIGEST,),
        )
        self.db.execute(
            "INSERT INTO symbols VALUES ('sym-1', 'src/app.py', 'f', 'function', 1, 0, 1, 8, 'run-1', 'confirmed_reference')"
        )

    def tearDown(self):
        self.db.close()

    def test_schema_version_and_foreign_keys(self):
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_resolved_relation_provenance(self):
        self.db.execute(
            """INSERT INTO relations
            (relation_id, relation_type, source_path, source_symbol_id, target_symbol_id,
             evidence_path, start_line, start_column, end_line, end_column, run_id,
             evidence, confidence_kind)
            VALUES ('r1', 'references', 'src/app.py', 'sym-1', 'sym-1',
                    'src/app.py', 1, 0, 1, 8, 'run-1', 'symbol occurrence', 'confirmed_reference')"""
        )
        self.assertEqual(
            self.db.execute("SELECT confidence_kind FROM relations WHERE target_symbol_id = 'sym-1'").fetchone()[0],
            "confirmed_reference",
        )

    def test_unresolved_target_cannot_be_marked_confirmed(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO relations
                (relation_id, relation_type, source_path, unresolved_target,
                 evidence_path, start_line, start_column, end_line, end_column,
                 run_id, evidence, confidence_kind)
                VALUES ('r2', 'calls', 'src/app.py', 'maybe', 'src/app.py',
                        1, 0, 1, 8, 'run-1', 'guess', 'confirmed_reference')"""
            )

    def test_unknown_symbol_and_wrong_layer_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO symbols VALUES ('bad', 'missing.py', 'f', 'function', 1, 0, 1, 2, 'run-1', 'unknown')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO coverage(layer,scope,status) VALUES ('understanding','all','available')")

    def test_independent_coverage_layers(self):
        for layer, status in [("snapshot", "available"), ("index", "not_indexed"),
                              ("graph", "unknown"), ("investigation", "unknown")]:
            self.db.execute("INSERT INTO coverage(layer, scope, status) VALUES (?, 'all', ?)", (layer, status))
        self.assertEqual(
            self.db.execute("SELECT layer FROM coverage ORDER BY layer").fetchall(),
            [("graph",), ("index",), ("investigation",), ("snapshot",)],
        )


if __name__ == "__main__":
    unittest.main()

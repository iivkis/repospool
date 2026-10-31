"""End-to-end dogfood freshness against real temporary Git history (no Rust needed)."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from repo_dogfood import check  # noqa: E402
from test_manifest_contract import fake_artifact  # noqa: E402
from rgp_reader import RgpError  # noqa: E402


class RepoDogfoodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.call("init", "-q")
        self.call("config", "user.name", "RGP Test")
        self.call("config", "user.email", "rgp-test@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "src/file.txt").write_bytes(b"alpha\nbeta\nbeta\n")
        self.call("add", "src/file.txt")
        self.call("commit", "-qm", "original source")
        self.source_commit = self.call("rev-parse", "HEAD").stdout.decode().strip()
        self.snapshot = self.repo / "REPO_CONTEXT.rgp"
        self.snapshot.write_bytes(fake_artifact(
            lambda m: m.update(source_commit=self.source_commit)))
        self.call("add", "REPO_CONTEXT.rgp")
        self.call("commit", "-qm", "publish only the RGP")
        self.publish_commit = self.call("rev-parse", "HEAD").stdout.decode().strip()

    def call(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True)

    def test_artifact_only_publication_is_fresh_even_with_different_head(self):
        report = check(self.repo)
        self.assertTrue(report["snapshot_complete"])
        self.assertTrue(report["snapshot_equivalent_to_checkout"])
        self.assertTrue(report["commit_difference_is_artifact_only"])
        self.assertEqual(report["source_commit"], self.source_commit)
        self.assertEqual(report["checkout_commit"], self.publish_commit)
        self.assertEqual(report["file_count"], 1)

    def test_single_file_publication_needs_no_sidecar(self):
        self.assertFalse((self.repo / "REPO_CONTEXT.rgpmi").exists())
        report = check(self.repo)
        self.assertTrue(report["snapshot_complete"])
        self.assertEqual(report["source_commit"], self.source_commit)

    def test_source_edit_after_publication_is_stale(self):
        (self.repo / "src/file.txt").write_text("changed after RGP publication\n")
        self.call("add", "src/file.txt")
        self.call("commit", "-qm", "modify source")
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            check(self.repo)

    def test_dirty_tracked_worktree_is_not_accepted_as_fresh(self):
        (self.repo / "src/file.txt").write_text("local edit not yet in Git\n")
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            check(self.repo)
        self.call("add", "src/file.txt")
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            check(self.repo)

    def test_untracked_source_is_not_in_rgp(self):
        (self.repo / "src/new.py").write_text("new function\n")
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            check(self.repo)

    def test_non_source_git_commit_does_not_magically_refresh_source(self):
        (self.repo / "AGENTS.md").write_text("New agent contract\n")
        self.call("add", "AGENTS.md")
        self.call("commit", "-qm", "modify docs")
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            check(self.repo)

    def test_modified_local_rgp_is_not_treated_as_trusted(self):
        with self.snapshot.open("ab") as output:
            output.write(b"uncommitted attacker bytes")
        with self.assertRaisesRegex(RgpError, "HASH_MISMATCH"):
            check(self.repo)

    def test_missing_rgp_refused(self):
        self.snapshot.unlink()
        with self.assertRaisesRegex(RgpError, "UNSUPPORTED_FORMAT"):
            check(self.repo)

    def test_unrelated_source_commit_rejected_even_if_source_text_same(self):
        original = self.source_commit
        self.call("checkout", "-qb", "unrelated", original)
        self.call("commit", "--allow-empty", "-qm", "different unrelated HEAD")
        other = self.call("rev-parse", "HEAD").stdout.decode().strip()
        # Explicitly use commit data from a diverged branch, then return.
        self.call("checkout", "-q", self.publish_commit)
        self.snapshot.write_bytes(fake_artifact(
            lambda m: m.update(source_commit=other)))
        self.call("add", "REPO_CONTEXT.rgp")
        self.call("commit", "-qm", "point snapshot at a non-ancestor")
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            check(self.repo)

    def test_export_materializes_only_checked_source(self):
        from repo_dogfood import export_workspace
        folder = self.repo / "outside-snapshot"
        report = check(self.repo)
        out = export_workspace(self.snapshot, folder, report["artifact_sha256"],
                               expected_commit=report["source_commit"])
        self.assertEqual(out["files_exported"], 1)
        self.assertEqual((folder / "src/file.txt").read_bytes(), b"alpha\nbeta\nbeta\n")


if __name__ == "__main__":
    unittest.main()

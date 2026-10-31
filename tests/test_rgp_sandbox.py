"""RGP-first sandbox source workspace: real bytes, safe output and provenance."""
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tests"))
from rgp_sandbox import METADATA, export_workspace as _export_workspace  # noqa: E402

def export_workspace(*args, **kwargs):
    """Existing test fixture is tied to the deliberately frozen fake commit."""
    return _export_workspace(*args, expected_commit="a" * 40,
                             attested_source_commit="a" * 40, **kwargs)

from rgp_reader import RgpError, Reader  # noqa: E402
from rgp_fixture import make_artifact, three_scope_artifact  # noqa: E402


class SandboxWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.artifact = self.root / "source.rgp"
        self.raw = three_scope_artifact()
        self.artifact.write_bytes(self.raw)
        self.trusted = hashlib.sha256(self.raw).hexdigest()

    def test_valid_authenticated_but_stale_snapshot_rejected(self):
        destination = self.root / "stale"
        with self.assertRaisesRegex(RgpError, "STALE_SNAPSHOT"):
            _export_workspace(self.artifact, destination, self.trusted,
                              expected_commit="b" * 40, attested_source_commit="a" * 40)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".rgp-stage-*")), [])
        with self.assertRaises(TypeError):
            _export_workspace(self.artifact, destination, self.trusted)
        for bad_commit in ("", "HEAD", "A" * 40, "f" * 39):
            with self.subTest(bad_commit=bad_commit), self.assertRaisesRegex(
                    RgpError, "UNSUPPORTED_FORMAT"):
                _export_workspace(self.artifact, destination, self.trusted,
                                  expected_commit=bad_commit)
        self.assertFalse(destination.exists())

    def test_correct_commit_report_avoids_claiming_live_github_check(self):
        destination = self.root / "matching"
        result = _export_workspace(self.artifact, destination, self.trusted,
                                   expected_commit="a" * 40, attested_source_commit="a" * 40)
        self.assertEqual(result["expected_commit"], "a" * 40)
        self.assertEqual(result["source_commit"], "a" * 40)
        self.assertEqual(result["commit_match"], "externally_attested")
        self.assertFalse(result["current_github_head_checked_by_exporter"])

    def test_real_reader_bytes_and_provenance_in_local_workspace(self):
        output = self.root / "worktree"
        report = export_workspace(self.artifact, output, self.trusted)
        self.assertEqual(report["source_commit"], "a" * 40)
        self.assertEqual(report["files_exported"], 4)
        self.assertTrue(report["snapshot_complete"])
        self.assertFalse(report["git_worktree"])
        self.assertEqual(report["rgp_sha256"], self.trusted)
        r = Reader(self.artifact)
        for item in r.manifest["files"]:
            path = output / item["path"]
            self.assertEqual(path.read_bytes(), r.read_bytes(item["path"]))
            self.assertTrue(stat.S_ISREG(path.lstat().st_mode))
            self.assertEqual(path.stat().st_mode & 0o111, 0)
        self.assertEqual(json.loads((output / METADATA).read_text()), report)
        self.assertFalse((output / ".git").exists())
        self.assertFalse((output / "REPO_CONTEXT.rgp").exists())

    def test_prefix_only_exports_requested_files(self):
        dst = self.root / "source-scope"
        result = export_workspace(self.artifact, dst, self.trusted, prefix="src/")
        self.assertEqual(result["files_exported"], 2)
        self.assertTrue((dst / "src/a.py").exists())
        self.assertTrue((dst / "src/sub/b.py").exists())
        self.assertFalse((dst / "tests").exists())
        self.assertFalse((dst / "src2").exists())
        self.assertEqual(result["prefix"], "src/")

    def test_out_of_band_hash_is_mandatory_and_no_partial_export(self):
        for bad in ("0" * 64, "invalid", "", self.trusted.upper()):
            target = self.root / "target"
            with self.subTest(hash=bad), self.assertRaises(RgpError):
                export_workspace(self.artifact, target, bad)
            self.assertFalse(target.exists())
        target = self.root / "corrupt"
        badfile = self.root / "truncated.rgp"
        badfile.write_bytes(self.raw[:-9])
        with self.assertRaisesRegex(RgpError, "TRUNCATED_ARTIFACT"):
            export_workspace(badfile, target, hashlib.sha256(badfile.read_bytes()).hexdigest())
        self.assertFalse(target.exists())

    def test_partial_snapshot_requires_explicit_opt_in(self):
        source = make_artifact({"src/file.txt": b"some content\n"},
                               excluded=(("secrets.txt", "sensitive_path"),))
        self.artifact.write_bytes(source)
        digest = hashlib.sha256(source).hexdigest()
        target = self.root / "out"
        with self.assertRaisesRegex(RgpError, "PARTIAL_SNAPSHOT"):
            export_workspace(self.artifact, target, digest)
        self.assertFalse(target.exists())
        report = export_workspace(self.artifact, target, digest, allow_partial=True)
        self.assertFalse(report["snapshot_complete"])
        self.assertEqual(report["unavailable_paths"], 1)
        self.assertEqual(report["files_exported"], 1)

    def test_existing_destination_and_resource_limits(self):
        output = self.root / "already"
        output.mkdir()
        (output / "keep").write_text("keep")
        with self.assertRaisesRegex(RgpError, "UNSAFE_PATH"):
            export_workspace(self.artifact, output, self.trusted)
        self.assertEqual((output / "keep").read_text(), "keep")
        for kwargs in ({"max_files": 3}, {"max_bytes": 2},
                       {"prefix": "../"}, {"prefix": "/etc"},
                       {"max_files": True}, {"max_bytes": -1}):
            target = self.root / "lim"
            with self.subTest(kwargs=kwargs), self.assertRaises(RgpError):
                export_workspace(self.artifact, target, self.trusted, **kwargs)
            self.assertFalse(target.exists())

    def test_source_symlink_is_never_materialized_as_actual_symlink(self):
        raw = make_artifact({"src/file.txt": b"relative/target\n"},
                            flags={"src/file.txt": "l"})
        self.artifact.write_bytes(raw)
        target = self.root / "link-text"
        result = export_workspace(self.artifact, target, hashlib.sha256(raw).hexdigest())
        path = target / "src/file.txt"
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        self.assertEqual(result["symlinks_materialized_as_regular_text_files"],
                         ["src/file.txt"])

    def test_source_executable_bit_is_not_materialized(self):
        raw = make_artifact({"src/file.txt": b"echo hello\n"},
                            flags={"src/file.txt": "x"})
        self.artifact.write_bytes(raw)
        target = self.root / "scripts"
        export_workspace(self.artifact, target, hashlib.sha256(raw).hexdigest())
        self.assertEqual((target / "src/file.txt").stat().st_mode & 0o111, 0)

    def test_metadata_collision_refused_and_no_workspace_created(self):
        raw = make_artifact({METADATA: b"collision\n"})
        self.artifact.write_bytes(raw)
        output = self.root / "blocked"
        with self.assertRaisesRegex(RgpError, "UNSAFE_PATH"):
            export_workspace(self.artifact, output, hashlib.sha256(raw).hexdigest())
        self.assertFalse(output.exists())

    def test_python_cli_creates_local_data_tree(self):
        target = self.root / "cli-output"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "python/rgp_sandbox.py"),
             "--artifact", str(self.artifact),
             "--trusted-sha256", self.trusted,
             "--expected-commit", "a" * 40,
             "--attested-source-commit", "a" * 40,
             "--output", str(target), "--prefix", "tests/"],
            capture_output=True, text=True, check=True)
        report = json.loads(completed.stdout)
        self.assertEqual(report["files_exported"], 1)
        self.assertEqual((target / "tests/test_a.py").read_bytes(), b"needle from tests\n")
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()

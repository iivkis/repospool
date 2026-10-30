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
from rgp_sandbox import METADATA, export_workspace  # noqa: E402
from rgp_reader import RgpError, Reader  # noqa: E402
from test_reader_api import three_scope_artifact  # noqa: E402
from test_manifest_contract import fake_artifact  # noqa: E402
from test_reader import fixture  # noqa: E402


class SandboxWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.artifact = self.root / "source.rgp"
        self.raw = three_scope_artifact()
        self.artifact.write_bytes(self.raw)
        self.trusted = hashlib.sha256(self.raw).hexdigest()

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
        source = fake_artifact(lambda manifest: manifest["excluded"].append(
            {"path": "secrets.txt", "reason": "sensitive_path"}))
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
        def as_symlink(manifest):
            entry = manifest["files"][0]
            entry["mode"] = "120000"
            entry["kind"] = "symlink"
            manifest["input_tree_digest"] = hashlib.sha256(
                entry["path"].encode() + b"\0" + b"120000\0" +
                bytes.fromhex(entry["sha256"])).hexdigest()
        raw = fake_artifact(as_symlink)
        self.artifact.write_bytes(raw)
        target = self.root / "link-text"
        result = export_workspace(self.artifact, target, hashlib.sha256(raw).hexdigest())
        path = target / "src/file.txt"
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        self.assertEqual(result["symlinks_materialized_as_regular_text_files"],
                         ["src/file.txt"])

    def test_source_executable_bit_is_not_materialized(self):
        def executable(manifest):
            entry = manifest["files"][0]
            entry["mode"] = "100755"
            manifest["input_tree_digest"] = hashlib.sha256(
                entry["path"].encode() + b"\0" + b"100755\0" +
                bytes.fromhex(entry["sha256"])).hexdigest()
        raw = fake_artifact(executable)
        self.artifact.write_bytes(raw)
        target = self.root / "scripts"
        export_workspace(self.artifact, target, hashlib.sha256(raw).hexdigest())
        self.assertEqual((target / "src/file.txt").stat().st_mode & 0o111, 0)

    def test_metadata_collision_refused_and_no_workspace_created(self):
        raw = fixture(path=METADATA)
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
             "--output", str(target), "--prefix", "tests/"],
            capture_output=True, text=True, check=True)
        report = json.loads(completed.stdout)
        self.assertEqual(report["files_exported"], 1)
        self.assertEqual((target / "tests/test_a.py").read_bytes(), b"needle from tests\n")
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()

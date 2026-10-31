#!/usr/bin/env python3
"""Validate and export the in-repository RGP snapshot for real agent work.

RGP source_commit may precede checkout HEAD by a commit changing ONLY
REPO_CONTEXT.rgp: the snapshot excludes itself, so no source code changed.
Never silently accept any other Git difference, an untracked/tampered RGP,
or a snapshot with unknown coverage. Python stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError, _require  # noqa: E402
from rgp_sandbox import export_workspace  # noqa: E402

NAME = "REPO_CONTEXT.rgp"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )


def checked_git(repo: Path, *args: str) -> bytes:
    result = git(repo, *args)
    _require(result.returncode == 0, "STALE_SNAPSHOT",
             f"git command failed ({' '.join(args)}): {result.stderr.decode('utf-8', 'replace')[:240]}")
    return result.stdout


def check(repo: Path, artifact: Path | None = None) -> dict:
    """Require a committed, unchanged RGP matching current HEAD source bytes.

    Do not infer freshness from the RGP's own SHA-256. The local repository
    checkout is the independent Git trust anchor and source of HEAD.
    """
    repo = Path(repo).resolve(strict=True)
    artifact = (repo / NAME) if artifact is None else Path(artifact)
    _require(artifact.absolute() == (repo / NAME), "UNSAFE_PATH",
             "the dogfood snapshot must be the repository-root REPO_CONTEXT.rgp")
    _require(artifact.is_file() and not artifact.is_symlink(),
             "UNSUPPORTED_FORMAT", "the tracked RGP file is absent or not a regular file")
    head = checked_git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    # Check that the *same actual bytes* exist at HEAD, not a modified local file.
    committed = checked_git(repo, "show", f"{head}:{NAME}")
    actual = artifact.read_bytes()
    _require(actual == committed, "HASH_MISMATCH",
             "local REPO_CONTEXT.rgp differs from committed Git blob")
    reader = Reader(actual)
    overview = reader.overview()
    # RGP/2.1 verifies its own guide, provenance header, manifest and source
    # hashes. Full artifact integrity is anchored independently in Git HEAD.
    _require(overview["snapshot_complete"], "PARTIAL_SNAPSHOT",
             "in-repository source snapshot is not complete")
    source = overview["source_commit"]
    if source is None and overview["transport"] == "RGP/3.0":
        # RGP/3.0 intentionally contains no commit/hash text. Recover the
        # source revision ONLY from independently trusted local Git history.
        # The publication bot makes a one-file RGP commit after source HEAD.
        parent_result = git(repo, "rev-parse", "HEAD^")
        source = head
        if parent_result.returncode == 0:
            parent = parent_result.stdout.decode().strip()
            changed = checked_git(repo, "diff", "--name-only", parent, head).decode().splitlines()
            if changed == [NAME]:
                source = parent
        # Check all included source bytes against Git at the chosen revision.
        # The RGP's in-memory derived SHA is never accepted as provenance.
        cursor = None
        seen = 0
        while True:
            page = reader.tree(limit=200, cursor=cursor)
            for path in page["paths"]:
                committed_source = checked_git(repo, "show", f"{source}:{path}")
                _require(reader.read_bytes(path) == committed_source, "HASH_MISMATCH",
                         f"RGP source differs from trusted Git: {path}")
                seen += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
        _require(seen == overview["file_count"], "HASH_MISMATCH", "RGP file index incomplete")
    checked_git(repo, "cat-file", "-e", f"{source}^{{commit}}")
    ancestor = git(repo, "merge-base", "--is-ancestor", source, head)
    _require(ancestor.returncode == 0, "STALE_SNAPSHOT",
             "RGP source commit is not an ancestor of checkout HEAD")
    # Only the tracked RGP is excluded from the input digest.
    # Publication may change only this file after source commit A. Other
    # changes (even docs and tests) make the snapshot stale.
    diff = git(repo, "diff", "--quiet", source, head,
               "--", ".", f":(exclude){NAME}")
    _require(diff.returncode == 0, "STALE_SNAPSHOT",
             "source files differ from the RGP commit; refresh the snapshot")
    # Local working-tree modifications are not represented by a pinned Git
    # snapshot, even when committed HEAD is source-equivalent. Refuse to
    # present the snapshot as a complete view of modified agent work.
    worktree = git(repo, "diff", "--quiet", "HEAD",
                   "--", ".", f":(exclude){NAME}")
    _require(worktree.returncode == 0, "STALE_SNAPSHOT",
             "tracked working-tree files differ from HEAD")
    staged = git(repo, "diff", "--cached", "--quiet", "HEAD",
                 "--", ".", f":(exclude){NAME}")
    _require(staged.returncode == 0, "STALE_SNAPSHOT",
             "staged source changes are not in the RGP")
    untracked = checked_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    _require(not untracked, "STALE_SNAPSHOT",
             "untracked source files are absent from the Git-pinned RGP")
    digest = hashlib.sha256(actual).hexdigest()
    return {
        "status": "PASS",
        "artifact": NAME,
        "artifact_sha256": digest,
        "source_commit": source,
        "checkout_commit": head,
        "snapshot_equivalent_to_checkout": True,
        "commit_difference_is_artifact_only": source != head,
        "snapshot_complete": True,
        "file_count": overview["file_count"],
        "included_source_bytes": overview["included_bytes"],
        "validation": "committed Git blob, Reader integrity, Git ancestry and all-source diff",
        "gate_b": "HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "export"):
        command = sub.add_parser(name)
        command.add_argument("--repo", type=Path, default=ROOT)
        if name == "export":
            command.add_argument("--output", required=True, type=Path)
            command.add_argument("--prefix", default="")
            command.add_argument("--max-files", type=int, default=1000)
            command.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
    args = parser.parse_args()
    try:
        report = check(args.repo)
        if args.command == "export":
            # check() has already established that the Git-tracked snapshot
            # represents HEAD's complete source tree, though source_commit
            # can differ by the inevitable artifact-only publication commit.
            output = export_workspace(
                Path(args.repo).resolve() / NAME, args.output,
                report["artifact_sha256"], expected_commit=report["source_commit"],
                attested_source_commit=report["source_commit"],
                prefix=args.prefix, max_files=args.max_files,
                max_bytes=args.max_bytes)
            report["export"] = {
                "directory": str(args.output),
                "files_exported": output["files_exported"],
                "bytes_exported": output["bytes_exported"],
            }
    except (OSError, RgpError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

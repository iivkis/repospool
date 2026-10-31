#!/usr/bin/env python3
"""Stage 1 acceptance: answer real source questions using a generated RGP.

No model, network, GitHub connector or third-party Python packages required.
This is an agent-owned *functional* check, not the comparative Gate B trial.
The emitted JSON contains only evidence metadata, never source bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader, RgpError  # noqa: E402

# Freeze task descriptions BEFORE inspecting the generated RGP.
# Their answers must agree with the original Git commit, not another RGP.
TASKS = (
    ("generator definition", "src/main.rs", "fn generate("),
    ("trusted Reader class", "python/rgp_reader.py", "class Reader:"),
    ("product stage 1", "docs/PRODUCT_ROADMAP.md", "## Этап 1."),
)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout


def run(*args: str) -> None:
    result = subprocess.run(list(args), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {args!r}\n"
            f"{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
        )


def check(repo: Path, rust_cli: Path) -> dict:
    commit = git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    evidence = {"status": "FAIL", "stage": 1, "source_commit": commit,
                "tasks": [], "checks": [], "gate_b": "HOLD"}
    with tempfile.TemporaryDirectory(prefix="rgp-stage1-") as d:
        work = Path(d)
        first, second, shared = (work / n for n in ("first.rgp", "second.rgp", "shared.rgp"))
        command = [str(rust_cli), "generate", "--repo", str(repo), "--rev", commit]
        run(*command, "--output", str(first))
        run(*command, "--output", str(second))
        data = first.read_bytes()
        assert data == second.read_bytes(), "nondeterministic RGP bytes"
        inline = Reader(first).overview()
        assert inline["transport"] == "RGP/0.1.0", "missing standalone transport"
        assert inline["embedded_instructions"], "missing inline agent instructions"
        assert inline["source_commit"] is None, "RGP must not contain Git hash"
        evidence["checks"].append("hash-free RGP; externally verified Git source")
        evidence["artifact_sha256"] = hashlib.sha256(data).hexdigest()
        evidence["artifact_bytes"] = len(data)
        evidence["checks"].append("deterministic RGP generated twice")
        run(str(rust_cli), "verify", "--artifact", str(first), "--repo", str(repo))
        reader = Reader(first)
        assert reader.validate()["valid"] is True
        assert reader.overview()["source_commit"] is None
        assert reader.coverage("index")["status"] == "not_indexed"
        assert reader.coverage("graph")["status"] == "not_indexed"
        assert reader.coverage("investigation")["status"] == "unknown"
        evidence["snapshot_status"] = reader.overview()["status"]
        evidence["checks"].append("Rust verify + Python validate + honest coverage")

        # Exercise tree, whole-file reads and line search through the RGP,
        # comparing their actual answers with Git at the *same pinned commit*.
        paths = set()
        cursor = None
        while True:
            page = reader.tree(limit=20, cursor=cursor)
            paths.update(page["paths"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(paths) == reader.overview()["file_count"], "tree pagination lost paths"
        for title, path, needle in TASKS:
            assert path in paths, f"source absent from snapshot: {path}"
            original = git(repo, "show", f"{commit}:{path}")
            decoded = original.decode("utf-8")
            result = reader.read(path)
            assert result["content"] == decoded, f"source mismatch: {path}"
            expected = [i for i, line in enumerate(decoded.splitlines(), 1) if needle in line]
            hits = reader.search(needle, paths=[path], limit=100)
            actual = [h["line"] for h in hits["matches"]]
            assert expected and actual == expected and hits["next_cursor"] is None, (
                f"incorrect answer for {title}: {actual} vs {expected}"
            )
            evidence["tasks"].append({
                "goal": title, "path": path, "source_sha256": hashlib.sha256(original).hexdigest(),
                "expected_lines": expected, "observed_lines": actual, "result": "PASS"
            })
        evidence["checks"].append("three predefined source tasks matched independent Git source")

        # Negative cases on the *actual* generated output, not only fixtures.
        oversized_index = re.sub(rb"index_bytes=[0-9]+", b"index_bytes=999999999",
                                 data, count=1)
        assert oversized_index != data
        for label, bad, code in (
            ("truncation", data[:-10], "TRUNCATED_ARTIFACT"),
            ("unknown version", data.replace(data.split(b"\n", 1)[0], b"RGP/9.0", 1), "UNSUPPORTED_FORMAT"),
            ("oversized index", oversized_index, "RESOURCE_LIMIT"),
        ):
            try:
                Reader(bad)
            except RgpError as exc:
                assert exc.code == code, f"{label}: unexpected error {exc.code}"
            else:
                raise AssertionError(f"{label}: invalid artifact accepted")
        try:
            reader.read("does-not-exist.src")
        except RgpError as exc:
            assert exc.code == "PARTIAL_SNAPSHOT"
        else:
            raise AssertionError("missing source unexpectedly readable")
        evidence["checks"].append("truncation, unsupported version, oversized index and absent file rejected")

        # Concurrent writers to the *same* destination must not share a
        # temporary filename. The published artifact must stay verifiable.
        children = [
            subprocess.Popen(command + ["--output", str(shared)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(3)
        ]
        for child in children:
            out, err = child.communicate(timeout=180)
            assert child.returncode == 0, f"parallel generation failed: {out!r} {err!r}"
        assert shared.read_bytes() == data
        assert not list(work.glob(".*.tmp")), "staging temp files left behind"
        run(str(rust_cli), "verify", "--artifact", str(shared), "--repo", str(repo))
        evidence["checks"].append("three concurrent writers produced a valid identical artifact")
    evidence["status"] = "PASS"
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--rust-cli", type=Path, default=ROOT / "target/debug/rgp")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = check(args.repo.resolve(), args.rust_cli.resolve())
    except Exception as exc:
        report = {"status": "FAIL", "stage": 1, "reason": str(exc),
                  "gate_b": "HOLD"}
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return 1
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

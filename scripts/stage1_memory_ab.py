#!/usr/bin/env python3
"""Paired old-vs-new Rust generator measurement on the exact same Git sources.

The CI job compiles both versions from clean source trees and runs each
against the same real temporary Git commit. RGP bytes MUST remain identical.
Peak RSS is measured by GNU time; no tokens or connector calls are inferred.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rgp_reader import Reader  # noqa: E402
from stage1_corpus import measured  # noqa: E402
from stage1_stress import make_source  # noqa: E402
from stage1_scale import FILES, CAP, SAMPLE, commit_many, path_for  # noqa: E402

PROFILES = ("many-files", "incompressible", "compressible", "scale-50k")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {"kind": "stage1_paired_same_runner_generator_memory",
              "status": "FAIL", "gate_b": "HOLD", "profiles": [],
              "limits": ["ephemeral runner, cache and invocation order may bias timings",
                         "no claims of connector, user latency or model token savings"]}
    try:
        for profile in PROFILES:
            with tempfile.TemporaryDirectory(prefix=f"rgp-memory-ab-{profile}-") as name:
                folder = Path(name)
                source_dir = folder / "source"
                if profile == "scale-50k":
                    commit = commit_many(source_dir, FILES)
                    sample = [path_for(index) for index in SAMPLE]
                else:
                    commit, fixture = make_source(source_dir, profile)
                    sample = list(fixture)[:3] + list(fixture)[-3:]
                # Alternate order to reduce, not eliminate, warm-cache bias.
                order = ("baseline", "candidate") if profile != "compressible" else (
                    "candidate", "baseline")
                paths: dict[str, Path] = {}
                times: dict[str, dict] = {}
                for arm in order:
                    binary = (args.baseline if arm == "baseline" else args.candidate).resolve()
                    artifact = folder / f"{arm}.rgp"
                    times[arm] = measured([
                        str(binary), "generate", "--repo", str(source_dir),
                        "--rev", commit, "--output", str(artifact),
                    ], folder / f"{arm}.time")
                    paths[arm] = artifact
                original = paths["baseline"].read_bytes()
                optimized = paths["candidate"].read_bytes()
                if original != optimized:
                    raise AssertionError(f"RGP/1.0 bytes changed on {profile}")
                for arm in ("baseline", "candidate"):
                    subprocess.run([
                        str((args.baseline if arm == "baseline" else args.candidate).resolve()),
                        "verify", "--artifact", str(paths[arm]), "--repo", str(source_dir),
                    ], check=True, stdout=subprocess.DEVNULL)
                reader = Reader(optimized)
                overview = reader.overview()
                if not reader.validate()["valid"] or overview["source_commit"] != commit:
                    raise AssertionError(f"invalid source snapshot: {profile}")
                if profile == "scale-50k":
                    if overview["snapshot_complete"] or overview["file_count"] != CAP:
                        raise AssertionError("large snapshot incorrectly claimed complete")
                    if overview["excluded_by_reason"].get("entry_limit") != FILES - CAP:
                        raise AssertionError("large source lost its exclusions")
                elif not overview["snapshot_complete"]:
                    raise AssertionError(f"unexpected partial snapshot: {profile}")
                # Check sampled source bytes against independently read pinned Git.
                for path in sorted(set(sample)):
                    raw = subprocess.check_output([
                        "git", "-C", str(source_dir), "show", f"{commit}:{path}"])
                    if reader._blob(path) != raw:
                        raise AssertionError(f"Git blob mismatch: {path}")
                baseline_rss = times["baseline"]["peak_rss_kib"]
                candidate_rss = times["candidate"]["peak_rss_kib"]
                if baseline_rss <= 0 or candidate_rss <= 0:
                    raise AssertionError("missing GNU time RSS")
                profile_report = {
                    "profile": profile, "source_commit": commit,
                    "same_artifact_sha256": hashlib.sha256(optimized).hexdigest(),
                    "rgp_bytes": len(optimized), "included_files": overview["file_count"],
                    "sampled_pinned_git_blobs": len(set(sample)),
                    "baseline": times["baseline"], "candidate": times["candidate"],
                    "peak_rss_change_percent": round(
                        100 * (candidate_rss - baseline_rss) / baseline_rss, 2),
                }
                report["profiles"].append(profile_report)
        # Synthetic large data and the maximum ZIP member count must benefit in RSS.
        for item in report["profiles"]:
            if item["profile"] in ("incompressible", "compressible", "scale-50k"):
                if item["candidate"]["peak_rss_kib"] >= item["baseline"]["peak_rss_kib"]:
                    raise AssertionError(f"no demonstrated memory gain on {item['profile']}")
        report["status"] = "PASS"
    except Exception as exc:
        report["error"] = str(exc)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

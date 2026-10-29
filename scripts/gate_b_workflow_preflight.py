#!/usr/bin/env python3
"""Fail-closed selection and verification for the MANUAL Gate B diagnostic workflow.

No model calls, no GitHub token handling and no production Gate B trial output.
The committed corpus manifest is the independent artifact integrity anchor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from rgp_reader import Reader  # noqa: E402
from gate_b import ProtocolError, digest_plan, strict_json, validate_plan

ID = re.compile(r'[a-z][a-z0-9_]{0,40}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def select(plan: dict, corpus: dict, task_id: str) -> dict:
    """Resolve one preregistered task to an externally anchored Actions artifact."""
    validate_plan(plan)
    if corpus.get('schema_version') != '1.0' or corpus.get('status') != 'PREPARED_NOT_BENCHMARKED':
        raise ProtocolError('invalid or unprepared corpus checkpoint')
    plan_hash = digest_plan(plan)
    if corpus.get('plan_sha256') != plan_hash:
        raise ProtocolError('corpus and preregistration plan digest differ')
    tasks = [task for task in plan['tasks'] if task['id'] == task_id]
    if len(tasks) != 1:
        raise ProtocolError('unknown or duplicated preregistered task')
    repo_id = tasks[0]['repository']
    if not ID.fullmatch(repo_id):
        raise ProtocolError('unsafe repository identifier')
    repo = next(r for r in plan['repositories'] if r['id'] == repo_id)
    matching = [a for a in corpus.get('artifacts', []) if isinstance(a, dict) and a.get('repository') == repo_id]
    if len(matching) != 1:
        raise ProtocolError('missing or duplicate corpus artifact')
    artifact = matching[0]
    if (artifact.get('source_commit') != repo['commit']
            or artifact.get('plan_sha256') != plan_hash
            or artifact.get('adapter_result') != 'PASS'
            or artifact.get('snapshot_complete') is not True):
        raise ProtocolError('corpus artifact was not verified against the pinned source')
    sha = artifact.get('sha256')
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ProtocolError('missing independent RGP SHA-256')
    size = artifact.get('rgp_bytes')
    if type(size) is not int or not 0 < size <= 128 * 1024 * 1024:
        raise ProtocolError('invalid recorded RGP size')
    return {
        'task_id': task_id,
        'repository': repo_id,
        'source_commit': repo['commit'],
        'plan_sha256': plan_hash,
        'artifact_name': 'gate-b-' + repo_id,
        'rgp_sha256': sha,
        'rgp_bytes': size,
        'agent_trials_eligible': False,
    }


def verify_download(target: dict, directory: Path) -> dict:
    path = directory / 'REPO_CONTEXT.rgp'
    if not path.is_file():
        raise ProtocolError('Actions download did not contain REPO_CONTEXT.rgp')
    if path.stat().st_size != target['rgp_bytes']:
        raise ProtocolError('downloaded RGP byte count mismatch')
    with path.open('rb') as stream:
        actual_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual_sha != target['rgp_sha256']:
        raise ProtocolError('downloaded RGP SHA-256 disagrees with committed corpus anchor')
    reader = Reader(path)
    if reader.manifest['source_commit'] != target['source_commit']:
        raise ProtocolError('RGP source commit mismatch')
    if not reader.overview()['snapshot_complete']:
        raise ProtocolError('RGP snapshot incomplete')
    provenance_path = directory / 'provenance.json'
    if provenance_path.exists():
        embedded = strict_json(provenance_path)
        if (embedded.get('rgp_sha256') != target['rgp_sha256']
                or embedded.get('source_commit') != target['source_commit']
                or embedded.get('plan_sha256') != target['plan_sha256']):
            raise ProtocolError('downloaded provenance conflicts with committed checkpoint')
    return {**target, 'verification': 'PASS', 'file_count': len(reader.manifest['files'])}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('select', 'verify'))
    p.add_argument('--task', required=True)
    p.add_argument('--plan', type=Path, default=Path('benchmarks/gate_b/plan.json'))
    p.add_argument('--corpus', type=Path, default=Path('benchmarks/gate_b/corpus_preparation.json'))
    p.add_argument('--download-dir', type=Path)
    p.add_argument('--github-env', type=Path)
    args = p.parse_args()
    try:
        target = select(strict_json(args.plan), strict_json(args.corpus), args.task)
        if args.command == 'verify':
            if args.download_dir is None:
                raise ProtocolError('--download-dir required for verify')
            target = verify_download(target, args.download_dir)
        if args.github_env:
            with args.github_env.open('a', encoding='utf-8') as env:
                env.write(f"GATE_B_ARTIFACT_NAME={target['artifact_name']}\n")
                env.write(f"GATE_B_RGP_SHA256={target['rgp_sha256']}\n")
        print(json.dumps(target, indent=2, sort_keys=True))
    except (ProtocolError, OSError, ValueError, KeyError) as e:
        p.error(str(e))


if __name__ == '__main__':
    main()

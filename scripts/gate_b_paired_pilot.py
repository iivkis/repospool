#!/usr/bin/env python3
"""Run a charged, non-scoring, paired OpenAI Responses diagnostic on pinned source.

Explicit confirmation is required. Uses GitHub REST and local RGP, NOT the
connected ChatGPT GitHub app. No output is eligible for Gate B scoring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from gate_b_agent_pilot import GitHubRest, PilotError, RgpLocal, network_json, run_agent

ARMS = ('github_rest_diagnostic', 'rgp_local_diagnostic')
OUTPUT_FILES = ('github-rest.json', 'rgp-local.json', 'paired-summary.json')


def prepare(plan: dict, task_id: str, rgp_path: Path, trusted_sha: str):
    """Check both backends and the immutable task without spending API tokens."""
    # Reuse the preregistered plan validator and its frozen SHA.
    from gate_b import digest_plan, validate_plan

    metadata = validate_plan(plan)
    matching = [task for task in plan['tasks'] if task['id'] == task_id]
    if len(matching) != 1:
        raise PilotError('unknown or duplicated preregistered task')
    task = matching[0]
    repo = next(r for r in plan['repositories'] if r['id'] == task['repository'])
    source = repo['commit']
    if not isinstance(trusted_sha, str) or len(trusted_sha) != 64 or any(c not in '0123456789abcdef' for c in trusted_sha):
        raise PilotError('trusted SHA-256 must be exactly 64 lowercase hex characters')
    started = time.monotonic_ns()
    rgp = RgpLocal(rgp_path, source, trusted_sha)
    validation_ms = (time.monotonic_ns() - started) // 1_000_000
    rest = GitHubRest(repo['github'], source, os.environ.get('GH_TOKEN'))
    return task, repo, {'github_rest_diagnostic': rest, 'rgp_local_diagnostic': rgp}, {
        'plan_sha256': metadata['plan_sha256'], 'task_id': task_id,
        'repository': repo['github'], 'source_commit': source,
        'rgp_sha256': trusted_sha, 'rgp_bytes': rgp.source_bytes,
        'rgp_local_validation_ms': validation_ms,
        'arms': list(ARMS), 'decision_eligible': False,
        'experiment_kind': 'diagnostic_not_gate_b',
    }


def exclusive_json(path: Path, value: dict) -> None:
    """Use O_EXCL and restrictive mode to avoid destroying expensive evidence."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write('\n')


def preflight_outputs(folder: Path) -> None:
    if folder.exists() and not folder.is_dir():
        raise PilotError('output path is not a directory')
    if any((folder / name).exists() for name in OUTPUT_FILES):
        raise PilotError('refusing to overwrite an existing diagnostic output')


def execute_pair(task: dict, repo: dict, backends: dict, context: dict,
                 *, model: str, token_budget: int, max_steps: int, output_dir: Path,
                 order: str, api) -> dict:
    """Write each finished arm immediately, retain partial result on second-arm failure."""
    if not model.strip() or token_budget < 32 or not 1 <= max_steps <= 12:
        raise PilotError('invalid model, budget, or max-steps')
    if order not in ('rest-first', 'rgp-first'):
        raise PilotError('invalid arm order')
    preflight_outputs(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    preflight_outputs(output_dir)
    chosen = ARMS if order == 'rest-first' else ARMS[::-1]
    summary = dict(context, model=model, token_budget=token_budget,
                   max_steps=max_steps, order=order, status='INCOMPLETE',
                   expected_arms=2, completed_arms=0, results={})
    for arm in chosen:
        try:
            result = run_agent(task, backends[arm], model, token_budget,
                               api=api, max_steps=max_steps)
            result.update({'arm': arm, 'repository': repo['github'],
                           'source_commit': repo['commit']})
            name = 'github-rest.json' if arm == ARMS[0] else 'rgp-local.json'
            exclusive_json(output_dir / name, result)
            summary['results'][arm] = {
                'path': name, 'total_tokens': result['total_tokens'],
                'tool_calls': len(result['tool_events']),
                'elapsed_ms': result['elapsed_ms'],
                'answer_sha256': result['answer_sha256'],
                'github_rest_http_requests': len(result['github_rest_requests']),
            }
            summary['completed_arms'] += 1
        except Exception as exc:
            # No false successful pair. Avoid recording provider error bodies/secrets.
            summary['failure'] = {'arm': arm, 'type': type(exc).__name__}
            exclusive_json(output_dir / 'paired-summary.json', summary)
            raise
    summary['status'] = 'COMPLETE_DIAGNOSTIC_ONLY'
    exclusive_json(output_dir / 'paired-summary.json', summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=Path('benchmarks/gate_b/plan.json'))
    parser.add_argument('--task', required=True)
    parser.add_argument('--rgp', type=Path, required=True)
    parser.add_argument('--trusted-rgp-sha256', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--token-budget', type=int, default=8000)
    parser.add_argument('--max-steps', type=int, default=4)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--order', choices=('rest-first', 'rgp-first'), default='rest-first')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--confirm-api-charges', action='store_true')
    args = parser.parse_args()
    try:
        if args.token_budget < 32 or not 1 <= args.max_steps <= 12:
            raise PilotError('token budget or max steps outside allowed range')
        preflight_outputs(args.output_dir)
        from gate_b import strict_json
        task, repo, backends, context = prepare(strict_json(args.plan), args.task,
                                                 args.rgp, args.trusted_rgp_sha256)
        preview = dict(context, model=args.model, order=args.order,
                       token_budget_per_arm=args.token_budget,
                       max_model_requests_per_arm=args.max_steps,
                       max_model_requests_for_pair=2 * args.max_steps,
                       api_charges_expected=not args.dry_run)
        if args.dry_run:
            print(json.dumps(preview, indent=2, ensure_ascii=False))
            return
        if not args.confirm_api_charges:
            raise PilotError('real model requests require --confirm-api-charges')
        if not os.environ.get('OPENAI_API_KEY'):
            raise PilotError('OPENAI_API_KEY not configured')
        def api(request):
            return network_json('https://api.openai.com/v1/responses',
                                token=os.environ['OPENAI_API_KEY'], data=request)[0]
        summary = execute_pair(task, repo, backends, context,
                               model=args.model, token_budget=args.token_budget,
                               max_steps=args.max_steps, output_dir=args.output_dir,
                               order=args.order, api=api)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    except (PilotError, OSError, ValueError) as exc:
        parser.error(f'{type(exc).__name__}: {exc}')


if __name__ == '__main__':
    main()

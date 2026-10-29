#!/usr/bin/env python3
"""Explicitly NON-GATE-B diagnostic agent comparison: GitHub REST vs local RGP.

Uses the OpenAI Responses API and standard-library-only function calling. Neither
arm is the ChatGPT GitHub connector; output is NEVER a 72-trial Gate B record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from rgp_reader import Reader  # noqa: E402

MAX_FILE_OUTPUT = 12_000
MAX_STEPS = 12
ARMS = ('github_rest_diagnostic', 'rgp_local_diagnostic')


class PilotError(ValueError):
    pass


def network_json(url: str, *, token: str | None = None, data: dict | None = None) -> tuple[dict, int]:
    headers = {'Accept': 'application/json', 'User-Agent': 'rgp-gate-b-diagnostic/1'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = None if data is None else json.dumps(data).encode('utf-8')
    if body is not None:
        headers['Content-Type'] = 'application/json'
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers, data=body), timeout=60) as reply:
        raw = reply.read(5_000_001)
        if len(raw) > 5_000_000:
            raise PilotError('provider response exceeds 5 MB')
        return json.loads(raw), len(raw)


class GitHubRest:
    """Pinned REST diagnostic backend, deliberately NOT the connected GitHub app."""

    def __init__(self, name: str, commit: str, token: str | None = None):
        if len(name.split('/')) != 2 or not all(x.replace('-', '').replace('_', '').isalnum() for x in name.split('/')):
            raise PilotError('invalid GitHub owner/repo')
        if len(commit) not in (40, 64) or any(c not in '0123456789abcdef' for c in commit):
            raise PilotError('Git commit must be pinned to a full SHA')
        self.name, self.commit, self.token = name, commit, token
        self.calls: list[dict] = []
        self._paths: list[str] | None = None

    def _get(self, url: str, *, raw: bool = False) -> bytes:
        t0 = time.monotonic_ns()
        headers = {'User-Agent': 'rgp-gate-b-diagnostic/1', 'Accept': 'application/vnd.github.raw+json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as res:
                data = res.read(4_000_001)
                if len(data) > 4_000_000:
                    raise PilotError('GitHub response exceeds 4 MB')
        except Exception as exc:
            self.calls.append({'operation': 'github_rest_get', 'success': False,
                               'transferred_bytes': 0, 'duration_ms': (time.monotonic_ns()-t0)//1_000_000,
                               'error': type(exc).__name__})
            raise
        self.calls.append({'operation': 'github_rest_get', 'success': True,
                           'transferred_bytes': len(data), 'duration_ms': (time.monotonic_ns()-t0)//1_000_000})
        return data

    def list_files(self, prefix: str, limit: int) -> dict:
        if self._paths is None:
            data = json.loads(self._get(f'https://api.github.com/repos/{self.name}/git/trees/{self.commit}?recursive=1'))
            if data.get('truncated'):
                raise PilotError('GitHub tree truncated')
            self._paths = sorted(f['path'] for f in data['tree'] if f['type'] == 'blob')
        return {'paths': [p for p in self._paths if p.startswith(prefix)][:limit],
                'limited': sum(p.startswith(prefix) for p in self._paths) > limit}

    def read_file(self, path: str, start_line: int, end_line: int) -> dict:
        if self._paths is None:
            self.list_files('', 100)
        if path not in self._paths:
            raise PilotError('path not in pinned Git tree')
        segments = [urllib.parse.quote(s, safe='') for s in path.split('/')]
        url = f'https://raw.githubusercontent.com/{self.name}/{self.commit}/' + '/'.join(segments)
        raw = self._get(url, raw=True)
        return lines_result(path, raw, start_line, end_line)


class RgpLocal:
    def __init__(self, path: Path, commit: str, sha256: str):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise PilotError('trusted external RGP SHA-256 mismatch')
        self.source_bytes = len(raw)
        self.reader = Reader(raw)
        if self.reader.manifest['source_commit'] != commit:
            raise PilotError('RGP source commit mismatch')
        if not self.reader.overview()['snapshot_complete']:
            raise PilotError('RGP snapshot is incomplete')
        self.calls: list[dict] = []

    def list_files(self, prefix: str, limit: int) -> dict:
        value = self.reader.tree(prefix=prefix, limit=limit)
        return {'paths': value['paths'], 'limited': value['next_cursor'] is not None}

    def read_file(self, path: str, start_line: int, end_line: int) -> dict:
        entry = self.reader.read(path, start_line=start_line, end_line=end_line)
        if entry['encoding'] != 'utf-8':
            raise PilotError('binary path cannot be returned as lines')
        return {'path': path, 'start_line': start_line, 'end_line': end_line, 'content': entry['content']}


def lines_result(path: str, raw: bytes, start_line: int, end_line: int) -> dict:
    try:
        lines = raw.decode('utf-8').splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise PilotError('non-UTF-8 source; diagnostic supports text only') from exc
    if start_line > len(lines) + 1 or end_line > len(lines):
        raise PilotError('line range beyond EOF')
    return {'path': path, 'start_line': start_line, 'end_line': end_line,
            'content': ''.join(lines[start_line-1:end_line])}


def tool_defs() -> list[dict]:
    return [
        {'type': 'function', 'name': 'list_files', 'description': 'List paths from the pinned repository. Results may be limited.',
         'strict': True, 'parameters': {'type': 'object', 'properties': {
             'prefix': {'type': 'string'}, 'limit': {'type': 'integer'}},
             'required': ['prefix', 'limit'], 'additionalProperties': False}},
        {'type': 'function', 'name': 'read_file', 'description': 'Read source lines from the pinned repository.',
         'strict': True, 'parameters': {'type': 'object', 'properties': {
             'path': {'type': 'string'}, 'start_line': {'type': 'integer'}, 'end_line': {'type': 'integer'}},
             'required': ['path', 'start_line', 'end_line'], 'additionalProperties': False}},
    ]


def run_agent(task: dict, backend, model: str, token_budget: int, *, api, max_steps: int = MAX_STEPS) -> dict:
    """Injectable Responses client, testable without network or an API key."""
    if token_budget <= 0 or max_steps <= 0:
        raise PilotError('invalid budget/steps')
    messages = [{'role': 'user', 'content': (
        'Use only the provided read-only repository tools. Cite actual source paths and line numbers. '
        'Do not guess code not yet read. Task: ' + task['prompt'])}]
    tools = tool_defs()
    usage = []
    tool_events = []
    answer = ''
    total_tokens = 0
    started = time.monotonic_ns()
    for step in range(max_steps):
        remaining = token_budget - total_tokens
        if remaining < 32:
            raise PilotError('model token budget exhausted')
        response = api({'model': model, 'instructions': 'You are a source-code analyst. Use tools for evidence; never invent citations.',
                        'input': messages, 'tools': tools, 'store': False,
                        'include': ['reasoning.encrypted_content'],
                        'max_output_tokens': min(2048, remaining)})
        if response.get('status') != 'completed':
            raise PilotError('model response not completed')
        receipt = response.get('usage')
        if not isinstance(receipt, dict) or type(receipt.get('input_tokens')) is not int or type(receipt.get('output_tokens')) is not int:
            raise PilotError('missing provider-reported token usage')
        receipt = {'input_tokens': receipt['input_tokens'], 'output_tokens': receipt['output_tokens'], 'response_id': response.get('id')}
        usage.append(receipt)
        total_tokens += receipt['input_tokens'] + receipt['output_tokens']
        if total_tokens > token_budget:
            raise PilotError('model token budget exceeded')
        output = response.get('output')
        if not isinstance(output, list):
            raise PilotError('missing model output')
        messages.extend(output)  # Retain reasoning/function-call items for stateless continuation.
        fn_calls = [o for o in output if o.get('type') == 'function_call']
        if not fn_calls:
            answer = '\n'.join(c.get('text', '') for o in output if o.get('type') == 'message'
                               for c in o.get('content', []) if c.get('type') == 'output_text')
            if not answer.strip():
                raise PilotError('no agent answer')
            break
        if len(tool_events) + len(fn_calls) > 30:
            raise PilotError('too many tool calls')
        for call in fn_calls:
            t0 = time.monotonic_ns()
            success = True
            try:
                args = json.loads(call['arguments'])
                if call['name'] == 'list_files':
                    prefix, limit = args['prefix'], args['limit']
                    if not isinstance(prefix, str) or type(limit) is not int or not 1 <= limit <= 100:
                        raise PilotError('invalid list parameters')
                    value = backend.list_files(prefix, limit)
                elif call['name'] == 'read_file':
                    path, start, end = args['path'], args['start_line'], args['end_line']
                    if (not isinstance(path, str) or path.startswith('/') or '\\' in path or
                        any(s in ('', '.', '..') for s in path.split('/')) or
                        type(start) is not int or type(end) is not int or not (1 <= start <= end <= start + 99)):
                        raise PilotError('invalid read parameters (max 100 lines)')
                    value = backend.read_file(path, start, end)
                else:
                    raise PilotError('unknown function')
                value_text = json.dumps(value, ensure_ascii=False)
                if len(value_text.encode('utf-8')) > MAX_FILE_OUTPUT:
                    raise PilotError('tool result exceeds 12KB')
            except (KeyError, ValueError, UnicodeError, urllib.error.URLError, OSError) as exc:
                success = False
                value_text = json.dumps({'error': type(exc).__name__ + ': ' + str(exc)[:180]})
            tool_events.append({'name': call['name'], 'arguments': call['arguments'],
                                'duration_ms': (time.monotonic_ns()-t0)//1_000_000,
                                'result_bytes': len(value_text.encode('utf-8')), 'success': success})
            messages.append({'type': 'function_call_output', 'call_id': call['call_id'], 'output': value_text})
    else:
        raise PilotError('max model steps exceeded')
    return {'model': model, 'task_id': task['id'], 'answer': answer,
            'answer_sha256': hashlib.sha256(answer.encode('utf-8')).hexdigest(),
            'provider_usage': usage, 'total_tokens': total_tokens,
            'tool_events': tool_events, 'elapsed_ms': (time.monotonic_ns()-started)//1_000_000,
            'github_rest_requests': getattr(backend, 'calls', []),
            'decision_eligible': False, 'experiment_kind': 'diagnostic_not_gate_b'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=Path('benchmarks/gate_b/plan.json'))
    parser.add_argument('--task', required=True)
    parser.add_argument('--arm', choices=ARMS, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--token-budget', type=int, default=16000)
    parser.add_argument('--rgp', type=Path)
    parser.add_argument('--trusted-rgp-sha256')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--confirm-api-charges', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('refusing to overwrite an existing diagnostic trace before model requests')
    if not args.confirm_api_charges:
        parser.error('real model requests require --confirm-api-charges')
    if not os.environ.get('OPENAI_API_KEY'):
        parser.error('OPENAI_API_KEY required for real diagnostic model runs')
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    tasks = [t for t in plan['tasks'] if t['id'] == args.task]
    if len(tasks) != 1:
        parser.error('task must exist exactly once in the frozen plan')
    task = tasks[0]
    repo = next(r for r in plan['repositories'] if r['id'] == task['repository'])
    if args.arm == 'github_rest_diagnostic':
        backend = GitHubRest(repo['github'], repo['commit'], os.environ.get('GH_TOKEN'))
    else:
        if not args.rgp or not args.trusted_rgp_sha256:
            parser.error('--rgp and --trusted-rgp-sha256 required for RGP arm')
        backend = RgpLocal(args.rgp, repo['commit'], args.trusted_rgp_sha256)
    def api(body: dict) -> dict:
        return network_json('https://api.openai.com/v1/responses', token=os.environ['OPENAI_API_KEY'], data=body)[0]
    outcome = run_agent(task, backend, args.model, args.token_budget, api=api)
    outcome.update({'arm': args.arm, 'repository': repo['github'], 'source_commit': repo['commit']})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(outcome, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'diagnostic answer saved: {args.output}; provider tokens: {outcome["total_tokens"]}')


if __name__ == '__main__':
    main()

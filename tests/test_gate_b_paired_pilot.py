"""Offline safety and resumability tests; no real agent API calls or Gate B trials."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gate_b_paired_pilot import (OUTPUT_FILES, PilotError, exclusive_json,
                                  execute_pair, preflight_outputs)


class Backend:
    calls = []

    def list_files(self, prefix, limit):
        return {'paths': ['src/main.rs'], 'limited': False}

    def read_file(self, path, start_line, end_line):
        return {'path': path, 'content': 'fn main() {}\n',
                'start_line': start_line, 'end_line': end_line}


class FakeApi:
    def __init__(self, fail_on=0):
        self.n = 0
        self.fail_on = fail_on

    def __call__(self, request):
        self.n += 1
        if self.n == self.fail_on:
            raise PilotError('offline injected failure')
        return {'id': f'resp-{self.n}', 'status': 'completed',
                'usage': {'input_tokens': 42, 'output_tokens': 18},
                'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': 'src/main.rs:1 fn main'}]}]}


class PairedPilotTest(unittest.TestCase):
    def setUp(self):
        self.task = {'id': 'rgp-git-snapshot', 'prompt': 'Find snapshot() function.'}
        self.repo = {'github': 'iivkis/rgp', 'commit': 'a' * 40}
        self.backends = {'github_rest_diagnostic': Backend(), 'rgp_local_diagnostic': Backend()}
        self.context = {'plan_sha256': 'b' * 64, 'source_commit': self.repo['commit'],
                        'decision_eligible': False, 'experiment_kind': 'diagnostic_not_gate_b'}

    def call(self, path, **kwargs):
        return execute_pair(self.task, self.repo, self.backends, self.context,
                            model='test-model-not-real', token_budget=1000,
                            max_steps=2, output_dir=path, order='rest-first',
                            api=kwargs.get('api', FakeApi()))

    def test_pair_captures_two_distinct_realistic_receipts(self):
        with tempfile.TemporaryDirectory() as td:
            result = self.call(Path(td))
            self.assertEqual(result['status'], 'COMPLETE_DIAGNOSTIC_ONLY')
            self.assertEqual(result['completed_arms'], 2)
            self.assertEqual({x['total_tokens'] for x in result['results'].values()}, {60})
            for name in OUTPUT_FILES:
                self.assertTrue((Path(td) / name).exists(), name)
            with open(Path(td) / 'paired-summary.json', encoding='utf-8') as stream:
                self.assertFalse(json.load(stream)['decision_eligible'])

    def test_preflight_rejects_existing_output_before_spending_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'paired-summary.json').write_text('old')
            api = FakeApi()
            with self.assertRaisesRegex(PilotError, 'overwrite'):
                self.call(Path(td), api=api)
            self.assertEqual(api.n, 0)

    def test_preserves_first_arm_if_second_fails(self):
        with tempfile.TemporaryDirectory() as td:
            api = FakeApi(fail_on=2)
            with self.assertRaisesRegex(PilotError, 'injected'):
                self.call(Path(td), api=api)
            self.assertTrue((Path(td) / 'github-rest.json').exists())
            self.assertFalse((Path(td) / 'rgp-local.json').exists())
            summary = json.loads((Path(td) / 'paired-summary.json').read_text())
            self.assertEqual(summary['status'], 'INCOMPLETE')
            self.assertEqual(summary['completed_arms'], 1)

    def test_alternate_order(self):
        with tempfile.TemporaryDirectory() as td:
            result = execute_pair(self.task, self.repo, self.backends, self.context,
                                  model='test-model', token_budget=1000,
                                  max_steps=2, output_dir=Path(td), order='rgp-first',
                                  api=FakeApi())
            self.assertEqual(result['order'], 'rgp-first')
            self.assertEqual(result['completed_arms'], 2)

    def test_exclusive_json_never_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'x.json'
            exclusive_json(dest, {'value': 1})
            with self.assertRaises(FileExistsError):
                exclusive_json(dest, {'value': 2})
            self.assertEqual(json.loads(dest.read_text()), {'value': 1})

    def test_budget_bounds_are_enforced_before_calls(self):
        with tempfile.TemporaryDirectory() as td:
            api = FakeApi()
            with self.assertRaisesRegex(PilotError, 'budget'):
                execute_pair(self.task, self.repo, self.backends, self.context,
                             model='test', token_budget=12, max_steps=2,
                             output_dir=Path(td), order='rest-first', api=api)
            self.assertEqual(api.n, 0)


if __name__ == '__main__':
    unittest.main()

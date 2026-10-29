"""Offline Responses-API fixtures. These are not real provider calls or Gate B runs."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from gate_b_agent_pilot import (GitHubRest, PilotError, RgpLocal, lines_result,
                                run_agent, tool_defs)


class Backend:
    calls = []

    def list_files(self, prefix, limit):
        return {'paths': ['src/main.rs'], 'limited': False}

    def read_file(self, path, start_line, end_line):
        return {'path': path, 'content': 'fn main() {}\n',
                'start_line': start_line, 'end_line': end_line}


class ResponseStub:
    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        assert request['store'] is False
        assert request['tools'] == tool_defs()
        assert request['max_output_tokens'] <= 2048
        if self.calls == 1:
            return {'id': 'resp-a', 'status': 'completed',
                    'usage': {'input_tokens': 100, 'output_tokens': 50},
                    'output': [{'type': 'function_call', 'call_id': 'call-1',
                                'name': 'read_file',
                                'arguments': '{"path":"src/main.rs","start_line":1,"end_line":1}'}]}
        assert any(x.get('type') == 'function_call_output' for x in request['input'])
        return {'id': 'resp-b', 'status': 'completed',
                'usage': {'input_tokens': 180, 'output_tokens': 40},
                'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': 'src/main.rs:1 contains main.'}]}]}


class PilotTest(unittest.TestCase):
    def setUp(self):
        self.task = {'id': 'x', 'prompt': 'Find the entry point.'}

    def test_two_step_agent_with_provider_receipts(self):
        stub = ResponseStub()
        result = run_agent(self.task, Backend(), 'test-only', 2000, api=stub)
        self.assertEqual(result['total_tokens'], 370)
        self.assertEqual(len(result['provider_usage']), 2)
        self.assertEqual(len(result['tool_events']), 1)
        self.assertEqual(stub.calls, 2)
        self.assertFalse(result['decision_eligible'])
        self.assertEqual(result['answer_sha256'], hashlib.sha256(result['answer'].encode()).hexdigest())

    def test_missing_provider_usage_never_defaults_to_zero(self):
        def fake(request):
            return {'status': 'completed', 'usage': None, 'output': []}
        with self.assertRaisesRegex(PilotError, 'provider-reported'):
            run_agent(self.task, Backend(), 'test-only', 2000, api=fake)

    def test_incomplete_response_rejected(self):
        def fake(request):
            return {'status': 'incomplete', 'usage': {'input_tokens': 5, 'output_tokens': 4}, 'output': []}
        with self.assertRaisesRegex(PilotError, 'not completed'):
            run_agent(self.task, Backend(), 'test-only', 2000, api=fake)

    def test_too_small_token_budget_rejected(self):
        with self.assertRaisesRegex(PilotError, 'budget exhausted'):
            run_agent(self.task, Backend(), 'test-only', 10, api=lambda _: None)

    def test_pinned_sha_and_safe_repo_path(self):
        with self.assertRaisesRegex(PilotError, 'pinned'):
            GitHubRest('example/repo', 'HEAD')
        with self.assertRaisesRegex(PilotError, 'invalid GitHub'):
            GitHubRest('../repo', 'a'*40)

    def test_line_ranges_are_not_approximate(self):
        self.assertEqual(lines_result('x', b'a\nb\n', 2, 2)['content'], 'b\n')
        with self.assertRaisesRegex(PilotError, 'beyond EOF'):
            lines_result('x', b'a\n', 2, 2)

    def test_invalid_trusted_artifact_rejected_before_reader(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td)/'sample.rgp'
            f.write_bytes(b'wrong')
            with self.assertRaisesRegex(PilotError, 'SHA-256 mismatch'):
                RgpLocal(f, 'a'*40, '0'*64)

    def test_output_for_diagnostic_is_never_gate_eligible(self):
        result = run_agent(self.task, Backend(), 'test-only', 2000, api=ResponseStub())
        self.assertEqual(result['experiment_kind'], 'diagnostic_not_gate_b')
        self.assertNotIn('experiment_kind:real_agent', str(result))


if __name__ == '__main__':
    unittest.main()

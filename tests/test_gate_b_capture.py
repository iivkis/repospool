"""Synthetic *unit* traces only; never count as measured Gate B agent runs."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gate_b import ProtocolError, digest_plan, evaluate  # noqa: E402
from gate_b_capture import compile_sessions, to_trial  # noqa: E402

PLAN = json.loads((ROOT / 'benchmarks/gate_b/plan.json').read_text())


def session(task=None, arm='github_connector'):
    task = task or PLAN['tasks'][0]
    source = next(r['commit'] for r in PLAN['repositories'] if r['id'] == task['repository'])
    return {
        'schema_version': '1.0', 'experiment_kind': 'real_agent',
        'plan_sha256': digest_plan(PLAN), 'task_id': task['id'],
        'repository': task['repository'], 'source_commit': source,
        'arm': arm, 'repetition': 1, 'model_id': 'test-only-model',
        'token_budget': 20000,
        'evidence_url': 'https://example.org/traces/synthetic-unit-test',
        'transport_verified': arm == 'rgp_actions_artifact',
        'trusted_artifact_sha256': 'a' * 64,
        'events': [
            {'type': 'connector_call', 'provider': 'GitHub', 'operation': 'fetch_file',
             'transferred_bytes': 64, 'duration_ms': 25, 'success': True},
            {'type': 'model_usage', 'source': 'provider_reported',
             'input_tokens': 150, 'output_tokens': 20},
            {'type': 'preparation', 'duration_ms': 10, 'transferred_or_generated_bytes': 100},
            {'type': 'run_summary', 'duration_ms': 800, 'peak_memory_bytes': 4096, 'errors': []},
            {'type': 'assessment', 'correct': True, 'assessor': 'unit-test',
             'rationale': 'synthetic testcase, not a real grading',
             'answer_sha256': hashlib.sha256(b'answer').hexdigest(),
             'missed_dependencies': 0, 'false_positives': 0},
        ],
    }


class TraceCaptureTest(unittest.TestCase):
    def test_metrics_derived_from_events(self):
        row = to_trial(PLAN, session(), trace_digest='f' * 64)
        self.assertEqual(row['total_tokens'], 170)
        self.assertEqual(row['connector_calls'], 1)
        self.assertEqual(row['connector_transferred_bytes'], 64)
        self.assertEqual(row['preparation_bytes'], 100)
        self.assertTrue(row['correct'])

    def test_missing_usage_is_not_zero(self):
        trace = session()
        trace['events'] = [ev for ev in trace['events'] if ev['type'] != 'model_usage']
        with self.assertRaisesRegex(ProtocolError, 'provider usage'):
            to_trial(PLAN, trace, trace_digest='f' * 64)

    def test_estimated_token_usage_rejected(self):
        trace = session()
        trace['events'][1]['source'] = 'estimated'
        with self.assertRaisesRegex(ProtocolError, 'provider-reported'):
            to_trial(PLAN, trace, trace_digest='f' * 64)

    def test_missing_trusted_transport_hash_rejected(self):
        trace = session(arm='rgp_actions_artifact')
        trace.pop('trusted_artifact_sha256')
        with self.assertRaisesRegex(ProtocolError, 'independently verified'):
            to_trial(PLAN, trace, trace_digest='f' * 64)

    def test_grade_with_missing_evidence_rejected(self):
        trace = session()
        trace['events'][-1]['answer_sha256'] = 'not-a-hash'
        with self.assertRaisesRegex(ProtocolError, 'SHA-256'):
            to_trial(PLAN, trace, trace_digest='f' * 64)

    def test_partial_runs_stay_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'one.json'
            path.write_text(json.dumps(session()))
            results = compile_sessions(PLAN, [path])
            self.assertEqual(results['trials'][0]['trace_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            verdict = evaluate(PLAN, results)
            self.assertEqual(verdict['status'], 'HOLD')
            self.assertEqual(verdict['received_trials'], 1)
            self.assertEqual(verdict['expected_trials'], 72)

    def test_mismatched_plan_rejected(self):
        trace = session()
        trace['plan_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ProtocolError, 'plan SHA'):
            to_trial(PLAN, trace, trace_digest='f' * 64)

    def test_mixed_models_rejected(self):
        other = session(arm='rgp_actions_artifact')
        other['model_id'] = 'different-model'
        with tempfile.TemporaryDirectory() as tmp:
            p1, p2 = Path(tmp) / 'a.json', Path(tmp) / 'b.json'
            p1.write_text(json.dumps(session()))
            p2.write_text(json.dumps(other))
            with self.assertRaisesRegex(ProtocolError, 'different model'):
                compile_sessions(PLAN, [p1, p2])


if __name__ == '__main__':
    unittest.main()

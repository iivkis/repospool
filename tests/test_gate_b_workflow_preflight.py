"""Offline validation of pinned/manual diagnostic workflow; never calls a model."""
import base64
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gate_b import ProtocolError, digest_plan  # noqa: E402
from gate_b_workflow_preflight import select, verify_download  # noqa: E402

# Unit tests use the local pinned RGP fixture and a synthetic corpus *checkpoint*
# tied to the real frozen task suite. These are not production agent trials.
PLAN_PATH = ROOT / 'benchmarks/gate_b/plan.json'
if not PLAN_PATH.exists():  # development-only source checkout
    PLAN_PATH = ROOT / 'plan.old.json'
PLAN = json.loads(PLAN_PATH.read_text())


def fixture(commit):
    path, data = 'src/example.py', b'hello world\n'
    digest = hashlib.sha256(data).hexdigest()
    tree = hashlib.sha256(path.encode() + b'\x00100644\x00' + bytes.fromhex(digest)).hexdigest()
    manifest = {'schema_version': '1.0', 'source_commit': commit, 'input_tree_digest': tree,
                'files': [{'path': path, 'mode': '100644', 'kind': 'file',
                           'size': len(data), 'sha256': digest, 'blob': 'files/' + digest,
                           'status': 'included'}],
                'excluded': [], 'index_coverage': 'not_indexed', 'graph_coverage': 'not_indexed'}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('files/' + digest, data)
    payload = stream.getvalue()
    text = base64.b64encode(payload)
    chunks = b''.join(text[i:i+76] + b'\n' for i in range(0, len(text), 76))
    header = (f'RGP/1.0\npayload_length={len(payload)}\npayload_sha256='
              f'{hashlib.sha256(payload).hexdigest()}\nbase64_line_width=76\n--\n').encode()
    return header + chunks + b'END RGP\n'


def example_checkpoint(plan):
    return {
        'schema_version': '1.0', 'status': 'PREPARED_NOT_BENCHMARKED',
        'plan_sha256': digest_plan(plan),
        'artifacts': [
            {
                'repository': repo['id'], 'source_commit': repo['commit'],
                'plan_sha256': digest_plan(plan), 'adapter_result': 'PASS',
                'snapshot_complete': True, 'sha256': 'a' * 64,
                'rgp_bytes': 100,
            } for repo in plan['repositories']
        ],
    }


class WorkflowPreflightTest(unittest.TestCase):
    def test_valid_selection(self):
        c = example_checkpoint(PLAN)
        selected = select(PLAN, c, 'rgp-transport-hash')
        self.assertEqual(selected['artifact_name'], 'gate-b-rgp')
        self.assertFalse(selected['agent_trials_eligible'])

    def test_wrong_plan_digest_rejected(self):
        c = example_checkpoint(PLAN)
        c['plan_sha256'] = '0' * 64
        with self.assertRaisesRegex(ProtocolError, 'digest differ'):
            select(PLAN, c, 'rgp-transport-hash')

    def test_unregistered_task_rejected(self):
        with self.assertRaisesRegex(ProtocolError, 'unknown or duplicated'):
            select(PLAN, example_checkpoint(PLAN), 'unknown')

    def test_missing_corresponding_artifact_rejected(self):
        c = example_checkpoint(PLAN)
        c['artifacts'] = [x for x in c['artifacts'] if x['repository'] != 'rgp']
        with self.assertRaisesRegex(ProtocolError, 'missing or duplicate'):
            select(PLAN, c, 'rgp-transport-hash')

    def test_unverified_artifact_rejected(self):
        c = example_checkpoint(PLAN)
        c['artifacts'][0]['adapter_result'] = 'FAIL'
        with self.assertRaisesRegex(ProtocolError, 'not verified'):
            select(PLAN, c, 'rgp-transport-hash')

    def test_wrong_sha_rejected(self):
        c = example_checkpoint(PLAN)
        c['artifacts'][0]['sha256'] = 'evil'
        with self.assertRaisesRegex(ProtocolError, 'independent'):
            select(PLAN, c, 'rgp-transport-hash')

    def test_bad_artifact_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'REPO_CONTEXT.rgp'
            path.write_bytes(b'corrupted')
            target = select(PLAN, example_checkpoint(PLAN), 'rgp-transport-hash')
            target['rgp_bytes'] = len(b'corrupted')
            with self.assertRaisesRegex(ProtocolError, 'SHA-256'):
                verify_download(target, Path(d))

    def test_valid_transport_and_untrusted_provenance_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'REPO_CONTEXT.rgp'
            commit = next(r['commit'] for r in PLAN['repositories'] if r['id'] == 'rgp')
            path.write_bytes(fixture(commit))
            c = example_checkpoint(PLAN)
            a = next(a for a in c['artifacts'] if a['repository'] == 'rgp')
            a['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            a['rgp_bytes'] = path.stat().st_size
            target = select(PLAN, c, 'rgp-transport-hash')
            self.assertEqual(verify_download(target, Path(d))['verification'], 'PASS')
            (Path(d) / 'provenance.json').write_text(json.dumps({'rgp_sha256': '0' * 64}))
            with self.assertRaisesRegex(ProtocolError, 'provenance conflicts'):
                verify_download(target, Path(d))


if __name__ == '__main__':
    unittest.main()

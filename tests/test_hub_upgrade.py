import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('runner', Path(__file__).resolve().parents[1] / 'upgrades/hub-workflow/runner.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
PLAN = {'sources': ['4.18.42'], 'stages': [
    {'version': '4.19.55', 'channel': 'stable-4.19'},
    {'version': '4.20.46', 'channel': 'stable-4.20'}]}


def cv(current='4.19.55', channel='stable-4.20', target='4.20.46'):
    return {'metadata': {'resourceVersion': '10', 'generation': 2},
            'spec': {'channel': channel, 'desiredUpdate': {'version': current, 'force': True}},
            'status': {'observedGeneration': 2,
                       'history': [{'version': current, 'state': 'Completed'}],
                       'availableUpdates': [{'version': target, 'image': 'quay.io/release@sha256:123'}]}}


class FakeAPI:
    def __init__(self, version):
        self.version, self.calls, self.jobs = version, [], []

    def call(self, method, path, data=None, patch_type=None):
        self.calls.append((method, path, data))
        if method == 'GET' and path.endswith('/jobs'):
            return {'items': self.jobs}
        if method == 'GET' and path == runner.CV:
            return self.version
        return {}


class Tests(unittest.TestCase):
    def workflow(self, version):
        api = FakeAPI(version)
        workflow = runner.Workflow(api, PLAN, '/api/v1/namespaces/local-cluster/configmaps/hub-upgrade-state')
        workflow.healthy = lambda: True
        return workflow, api

    def test_skip_completed_intermediate(self):
        self.assertEqual(runner.remaining(PLAN, '4.19.55'), PLAN['stages'][1:])

    def test_no_downgrade_or_replay(self):
        self.assertEqual(runner.remaining(PLAN, '4.20.46'), [])
        with self.assertRaises(runner.Blocked):
            runner.remaining(PLAN, '4.21.0')

    def test_no_force_and_digest_only(self):
        workflow, api = self.workflow(cv())
        self.assertFalse(workflow.step())
        patch = [c for c in api.calls if c[0] == 'PATCH' and c[1] == runner.CV][0][2]
        self.assertEqual(patch[0]['op'], 'test')
        self.assertNotIn('force', patch[1]['value'])
        self.assertIn('@sha256:', patch[1]['value']['image'])

    def test_channel_selected_before_release(self):
        workflow, api = self.workflow(cv(channel='stable-4.18'))
        workflow.step()
        patch = [c for c in api.calls if c[0] == 'PATCH' and c[1] == runner.CV][0][2]
        self.assertEqual(patch[1]['path'], '/spec/channel')

    def test_unknown_release_never_forced(self):
        version = cv()
        version['status']['availableUpdates'] = []
        workflow, api = self.workflow(version)
        workflow.step()
        self.assertFalse(any(c[0] == 'PATCH' and c[1] == runner.CV for c in api.calls))

    def test_restart_observes_inflight_update(self):
        version = cv()
        version['spec']['desiredUpdate']['version'] = '4.20.46'
        workflow, api = self.workflow(version)
        workflow.step()
        self.assertFalse(any(c[0] == 'PATCH' and c[1] == runner.CV for c in api.calls))

    def test_external_update_blocks(self):
        version = cv()
        version['spec']['desiredUpdate']['version'] = '4.20.47'
        workflow, _ = self.workflow(version)
        with self.assertRaises(runner.Blocked):
            workflow.step()

    def test_curator_concurrency_blocks(self):
        workflow, api = self.workflow(cv())
        api.jobs = [{'metadata': {'name': 'curator-job-test'}, 'status': {'active': 1}}]
        with self.assertRaises(runner.Blocked):
            workflow.step()

    def test_completed_campaign_does_not_request_update(self):
        workflow, api = self.workflow(cv(current='4.20.46'))
        self.assertTrue(workflow.step())
        self.assertFalse(any(c[0] == 'PATCH' and c[1] == runner.CV for c in api.calls))


if __name__ == '__main__':
    unittest.main()

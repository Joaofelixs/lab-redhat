"""Run an explicitly approved hub upgrade plan using the Kubernetes API."""

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path


class Blocked(Exception):
    pass


def completed(cv):
    history = cv.get('status', {}).get('history', [])
    return next((h['version'] for h in history if h['state'] == 'Completed'), None)


def remaining(plan, current):
    versions = [s['version'] for s in plan['stages']]
    if current in versions:
        return plan['stages'][versions.index(current) + 1:]
    if current not in plan['sources']:
        raise Blocked('Current version is not an approved source: ' + str(current))
    return plan['stages']


def condition(obj, name, value):
    return any(c['type'] == name and c['status'] == value
               for c in obj.get('status', {}).get('conditions', []))


def release(cv, version):
    if condition(cv, 'RetrievedUpdates', 'False'):
        return None
    return next((r for r in cv.get('status', {}).get('availableUpdates', []) or []
                 if r['version'] == version and '@sha256:' in r['image']), None)


class API:
    def __init__(self):
        self.base = 'https://' + os.environ['KUBERNETES_SERVICE_HOST'] + ':' + os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')
        self.token = Path('/var/run/secrets/kubernetes.io/serviceaccount/token')
        self.context = ssl.create_default_context(cafile='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')

    def call(self, method, path, data=None, patch_type='application/merge-patch+json'):
        headers = {'Authorization': 'Bearer ' + self.token.read_text().strip()}
        if data is not None:
            headers['Content-Type'] = patch_type if method == 'PATCH' else 'application/json'
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=None if data is None else json.dumps(data).encode())
        with urllib.request.urlopen(req, context=self.context, timeout=30) as response:
            return json.load(response)


CV = '/apis/config.openshift.io/v1/clusterversions/version'


class Workflow:
    def __init__(self, api, plan, checkpoint, timeout=43200):
        self.api, self.plan, self.checkpoint = api, plan, checkpoint
        self.deadline = time.monotonic() + timeout

    def wait(self):
        if time.monotonic() >= self.deadline:
            raise Blocked('Campaign deadline exceeded; inspect state before retrying')
        time.sleep(30)

    def request(self, method, path, data=None, patch_type='application/merge-patch+json'):
        # API interruptions are expected when the hub SNO reboots. Never retry blindly
        # after a PATCH: the outer loop rereads the actual ClusterVersion first.
        return self.api.call(method, path, data, patch_type)

    def state(self, phase, version):
        self.request('PATCH', self.checkpoint, {'data': {
            'phase': phase, 'version': version,
            'plan': json.dumps(self.plan, sort_keys=True)}})
        print(phase + ': ' + version, flush=True)

    def healthy(self):
        operators = self.request('GET', '/apis/config.openshift.io/v1/clusteroperators')['items']
        if not operators:
            return False
        for operator in operators:
            if condition(operator, 'Upgradeable', 'False'):
                raise Blocked('Upgradeable=False: ' + operator['metadata']['name'])
            if not (condition(operator, 'Available', 'True') and
                    condition(operator, 'Degraded', 'False') and
                    condition(operator, 'Progressing', 'False')):
                return False
        nodes = self.request('GET', '/api/v1/nodes')['items']
        pools = self.request('GET', '/apis/machineconfiguration.openshift.io/v1/machineconfigpools')['items']
        return bool(nodes) and bool(pools) and all(condition(n, 'Ready', 'True') for n in nodes) and all(
            condition(p, 'Updated', 'True') and not condition(p, 'Degraded', 'True')
            and not condition(p, 'Updating', 'True') for p in pools)

    def step(self):
        namespace_path = self.checkpoint.rsplit('/configmaps/', 1)[0]
        jobs = self.request('GET', namespace_path.replace('/api/v1/', '/apis/batch/v1/') + '/jobs')['items']
        if any(j['metadata']['name'].startswith('curator-job-') and
               j.get('status', {}).get('active', 0) for j in jobs):
            raise Blocked('An active ClusterCurator Job exists; do not run two engines')
        cv = self.request('GET', CV)
        current = completed(cv)
        stages = remaining(self.plan, current)
        if not stages:
            if condition(cv, 'Progressing', 'True') or not self.healthy():
                return False
            self.state('Completed', current)
            return True
        stage = stages[0]
        desired = cv.get('spec', {}).get('desiredUpdate', {}).get('version', current)
        if desired != current or condition(cv, 'Progressing', 'True'):
            if desired != stage['version']:
                raise Blocked('Another update is in progress: ' + str(desired))
            self.state('WaitingForCompletion', desired)
            return False
        if condition(cv, 'Failing', 'True') or not self.healthy():
            return False
        if cv['spec'].get('channel') != stage['channel']:
            self.state('SelectingChannel', stage['version'])
            self.request('PATCH', CV, [
                {'op': 'test', 'path': '/metadata/resourceVersion', 'value': cv['metadata']['resourceVersion']},
                {'op': 'add', 'path': '/spec/channel', 'value': stage['channel']}
            ], 'application/json-patch+json')
            return False
        if cv.get('status', {}).get('observedGeneration') != cv['metadata']['generation']:
            return False
        target = release(cv, stage['version'])
        if not target:
            self.state('WaitingForRecommendedUpdate', stage['version'])
            return False
        self.state('RequestingUpdate', stage['version'])
        self.request('PATCH', CV, [
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': cv['metadata']['resourceVersion']},
            {'op': 'add', 'path': '/spec/desiredUpdate', 'value': {
                'version': target['version'], 'image': target['image']}}
        ], 'application/json-patch+json')
        return False

    def run(self):
        while True:
            try:
                if self.step():
                    return
            except urllib.error.HTTPError as exc:
                if exc.code not in (409, 422, 429, 500, 502, 503, 504):
                    raise Blocked('API request failed: HTTP ' + str(exc.code)) from exc
                print('API temporarily unavailable or concurrent change: HTTP ' + str(exc.code), flush=True)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                print('Waiting for API recovery: ' + type(exc).__name__, flush=True)
            self.wait()


if __name__ == '__main__':
    namespace = Path('/var/run/secrets/kubernetes.io/serviceaccount/namespace').read_text().strip()
    plan = json.loads(Path('/plan/plan.json').read_text())
    checkpoint = '/api/v1/namespaces/' + namespace + '/configmaps/hub-upgrade-state'
    Workflow(API(), plan, checkpoint).run()

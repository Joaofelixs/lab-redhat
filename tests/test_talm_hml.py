import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / 'talm'


def documents(path):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


class HmlSafetyTests(unittest.TestCase):
    def test_selection_is_hml_only(self):
        placement = documents('policies/selection.yaml')[0]
        self.assertEqual(placement['spec']['clusterSets'], ['hml'])
        labels = placement['spec']['predicates'][0]['requiredClusterSelector']['labelSelector']['matchLabels']
        self.assertEqual(labels['name'], 'cluster-2dns8')
        self.assertEqual(labels['cluster.open-cluster-management.io/clusterset'], 'hml')
        for path in ['campaigns/01-upgrade-4-19-55.yaml', 'campaigns/02-upgrade-4-20-46.yaml']:
            cgu = documents(path)[0]
            self.assertIs(cgu['spec']['enable'], False)
            self.assertIs(cgu['spec']['preCaching'], False)
            self.assertEqual(cgu['spec']['clusterLabelSelectors'][0]['matchLabels'], labels)
            self.assertNotIn('clusters', cgu['spec'])
            self.assertEqual(cgu['spec']['remediationStrategy']['canaries'], ['cluster-2dns8'])

    def test_policies_cannot_remediate_on_their_own(self):
        for path in ['policies/upgrade-4-19-55.yaml', 'policies/upgrade-4-20-46.yaml']:
            policy = documents(path)[0]
            self.assertEqual(policy['spec']['remediationAction'], 'inform')
            cfg = policy['spec']['policy-templates'][0]['objectDefinition']
            self.assertEqual(cfg['spec']['remediationAction'], 'inform')
            cv = cfg['spec']['object-templates'][0]['objectDefinition']
            self.assertTrue(cv['spec']['upstream'].startswith('https://'))
            self.assertTrue(cv['spec']['channel'])
            self.assertFalse(cv['spec']['desiredUpdate'].get('force', False))
            self.assertEqual(cv['status']['history'][0]['state'], 'Completed')

    def test_dependency_and_manual_sync(self):
        second = documents('campaigns/02-upgrade-4-20-46.yaml')[0]
        self.assertEqual(second['spec']['blockingCRs'], [{'name': 'fleet-4-19-55', 'namespace': 'talm-upgrades'}])
        for path in ['applications/policies.yaml', 'applications/campaigns.yaml']:
            app = documents(path)[0]
            self.assertIs(app['spec']['syncPolicy']['automated']['enabled'], False)

    def test_binding_installed_by_administrator(self):
        binding = documents('bootstrap/cluster-set-binding.yaml')[0]
        self.assertEqual(binding['metadata']['name'], 'hml')
        self.assertEqual(binding['spec']['clusterSet'], 'hml')
        self.assertFalse(any(d['kind'] == 'ManagedClusterSetBinding' for d in documents('policies/selection.yaml')))


if __name__ == '__main__':
    unittest.main()

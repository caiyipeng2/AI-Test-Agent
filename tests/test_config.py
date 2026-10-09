import json
import tempfile
import unittest
from pathlib import Path

try:
    from ai_test_agent.config import load_config, validate_config
except ImportError:
    load_config = validate_config = None


class ConfigTests(unittest.TestCase):
    def fixture(self):
        return {
            'adb_path': 'tools/adb.exe', 'serial': 'reference-serial',
            'device_alias': 'reference-high', 'package': 'com.example.game',
            'environment': 'test', 'environment_source': 'approved artifact verification',
            'artifact_root': 'artifacts', 'startup_wait_seconds': 0,
        }

    def test_rejects_production_target(self):
        self.assertIsNotNone(validate_config, 'configuration contract is not implemented')
        data = self.fixture()
        data['environment'] = 'production'
        self.assertTrue(validate_config(data))

    def test_requires_explicit_device_and_environment_source(self):
        self.assertIsNotNone(validate_config, 'configuration contract is not implemented')
        for key in ('serial', 'environment_source'):
            data = self.fixture()
            data.pop(key)
            self.assertTrue(validate_config(data), key)

    def test_rejects_invalid_budget_and_unsafe_alias(self):
        self.assertIsNotNone(validate_config, 'configuration contract is not implemented')
        for key, value in [('startup_wait_seconds', -1), ('startup_wait_seconds', float('nan')),
                           ('startup_wait_seconds', 600), ('device_alias', '../other')]:
            data = self.fixture()
            data[key] = value
            self.assertTrue(validate_config(data), (key, value))

    def test_loads_config_without_guessing_device(self):
        self.assertIsNotNone(load_config, 'configuration contract is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            path.write_text(json.dumps(self.fixture()), encoding='utf-8')
            result = load_config(path)
            self.assertEqual(result['serial'], 'reference-serial')
            self.assertEqual(result['environment'], 'test')

    def test_invalid_types_return_validation_errors(self):
        self.assertIsNotNone(validate_config, 'configuration contract is not implemented')
        for key in ('serial', 'package', 'device_alias'):
            data = self.fixture()
            data[key] = None
            self.assertTrue(validate_config(data), key)

    def test_action_wait_budget_is_bounded(self):
        self.assertIsNotNone(validate_config, 'configuration contract is not implemented')
        data = self.fixture()
        data['action_wait_seconds'] = 10000
        self.assertTrue(validate_config(data))


if __name__ == '__main__':
    unittest.main()

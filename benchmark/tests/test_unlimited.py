import copy
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from story_benchmark.budget import BUDGET_FIELDS, validate_budget_policy
from story_benchmark.experiment import load_experiment
from story_benchmark.io import BenchmarkError
from story_benchmark.runner import execute_run, resume_export, validate_config
from test_v3 import Fixture

ROOT = Path(__file__).resolve().parents[1]


class UnlimitedTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(budget_mode='unlimited', live=True, model='frozen',
                           model_base_url='https://example.test/v1',
                           **dict.fromkeys(BUDGET_FIELDS))

    def test_explicit_unlimited_accepted(self):
        self.assertEqual(validate_config(self.config), [])

    def test_missing_or_finite_unlimited_fields_rejected(self):
        for field in BUDGET_FIELDS:
            for value in ('missing', 1, False, 'null', float('inf')):
                with self.subTest(field=field, value=value):
                    config = dict(self.config)
                    if value == 'missing':
                        config.pop(field)
                    else:
                        config[field] = value
                    self.assertIn('unlimited_requires_explicit_null_limit:' + field,
                                  validate_config(config))

    def test_unknown_policy_rejected(self):
        self.assertIn('invalid_budget_mode', validate_budget_policy({'budget_mode': None}))
        self.assertIn('invalid_budget_mode', validate_budget_policy({'budget_mode': 'infinite'}))

    def test_legacy_missing_limits_still_rejected(self):
        self.config.pop('budget_mode')
        self.assertTrue(any('missing_positive_limit' in e for e in validate_config(self.config)))

    def test_bounded_policy_preserved(self):
        self.config.update(budget_mode='bounded', timeout_seconds=1800, max_calls=160,
                           max_output_tokens=16384, max_input_chars=200000)
        self.assertEqual(validate_config(self.config), [])
        self.config.pop('budget_mode')
        self.assertEqual(validate_config(self.config), [])
        self.config['timeout_seconds'] = float('inf')
        self.assertIn('missing_positive_limit: timeout_seconds', validate_config(self.config))

    def experiment(self, root, data):
        path = root / 'experiment.json'
        path.write_text(json.dumps(data))
        return load_experiment(path)

    def test_example_all_three_receive_same_unlimited_policy(self):
        configs = load_experiment(ROOT / 'configs/experiment.v3.example.json')
        for config in configs.values():
            self.assertEqual(config['budget_mode'], 'unlimited')
            self.assertEqual(validate_budget_policy(config), [])
            self.assertTrue(all(config[field] is None for field in BUDGET_FIELDS))

    def test_system_cannot_override_any_common_budget_condition(self):
        data = json.loads((ROOT / 'configs/experiment.v3.example.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            for system in data['systems']:
                for field in ('budget_mode', *BUDGET_FIELDS):
                    with self.subTest(system=system, field=field):
                        edited = copy.deepcopy(data)
                        edited['systems'][system][field] = None
                        with self.assertRaisesRegex(BenchmarkError, 'per_system_override'):
                            self.experiment(Path(temp), edited)

    def test_common_mixed_policy_rejected_before_run(self):
        data = json.loads((ROOT / 'configs/experiment.v3.example.json').read_text())
        data['common']['max_calls'] = 160
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(BenchmarkError, 'unlimited_requires_explicit_null_limit:max_calls'):
                self.experiment(Path(temp), data)

    def test_unlimited_runner_arms_no_timer_and_seals_same_result(self):
        config = {**self.config, 'live': False}
        with tempfile.TemporaryDirectory() as temp, patch('story_benchmark.runner.signal.setitimer') as timer:
            root = Path(temp)
            # Unlimited generation also works outside the POSIX main thread.
            with ThreadPoolExecutor(max_workers=1) as executor:
                for system in ('if_line', 'ai4visualnovel', 'infiplot'):
                    result = executor.submit(execute_run, system, ROOT / 'examples/CAMPUS-01-V3',
                                             config, root / system, Fixture(system), True).result()
                    self.assertEqual(result['outcome'], 'completed', result['errors'])
                    self.assertEqual(resume_export(root / system), result)
                    manifest = json.loads((root / system / 'manifest.json').read_text())
                    self.assertEqual(manifest['budget_mode'], 'unlimited')
                    self.assertIn('native/provider limits preserved', manifest['budget_policy'])
            timer.assert_not_called()


if __name__ == '__main__':
    unittest.main()

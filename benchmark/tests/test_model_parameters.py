import copy
import json
from pathlib import Path
import tempfile
import unittest

from story_benchmark.audit import audit_trace
from story_benchmark.experiment import load_experiment
from story_benchmark.io import BenchmarkError, atomic_json
from story_benchmark.model_parameters import apply_model_parameters, validate_model_parameters


class CommonModelParametersTest(unittest.TestCase):
    def test_explicit_controls_preserve_native_story_and_schema(self):
        native = {'model': 'same-model', 'messages': [{'role': 'user', 'content': '共同题目\n固定开头'}],
                  'response_format': {'type': 'json_schema', 'json_schema': {'name': 'native'}},
                  'max_tokens': 8000, 'temperature': 0.7}
        before = copy.deepcopy(native)
        parameters = {'thinking': {'type': 'disabled'}}
        actual = apply_model_parameters(native, parameters)
        self.assertEqual(native, before)
        self.assertEqual({k: v for k, v in actual.items() if k != 'thinking'}, native)
        actual['messages'][0]['content'] = 'mutated'
        actual['thinking']['type'] = 'enabled'
        self.assertEqual(native, before)
        self.assertEqual(parameters, {'thinking': {'type': 'disabled'}})

    def test_story_schema_model_and_budget_cannot_be_overridden(self):
        for field in ('messages', 'prompt', 'system', 'response_format', 'model', 'base_url',
                      'max_tokens', 'max_completion_tokens', 'temperature'):
            with self.subTest(field=field), self.assertRaises(BenchmarkError):
                validate_model_parameters({field: 'forbidden'})
        for invalid in (None, [], {'thinking': True}, {'thinking': {'type': 'disabled', 'prompt': 'x'}},
                        {'thinking': {'type': 'unknown'}}, {'reasoning_effort': []},
                        {'thinking': {'type': 'disabled'}, 'reasoning_effort': 'high'}):
            with self.subTest(parameters=invalid), self.assertRaises(BenchmarkError):
                validate_model_parameters(invalid)

    def test_one_common_setting_and_backward_compatible_empty_default(self):
        example = Path(__file__).resolve().parents[1] / 'configs/experiment.example.json'
        config = json.loads(example.read_text())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'experiment.json'
            config['common'].pop('model_parameters')
            atomic_json(path, config)
            self.assertTrue(all(c['model_parameters'] == {} for c in load_experiment(path).values()))
            config['common']['model_parameters'] = {'thinking': {'type': 'disabled'}}
            atomic_json(path, config)
            self.assertTrue(all(c['model_parameters'] == config['common']['model_parameters']
                                for c in load_experiment(path).values()))
            config['systems']['infiplot']['model_parameters'] = {}
            atomic_json(path, config)
            with self.assertRaisesRegex(BenchmarkError, 'per_system_override'):
                load_experiment(path)

    def test_audit_checks_every_http_request_not_only_config_or_first_call(self):
        parameters = {'thinking': {'type': 'disabled'}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            atomic_json(root / 'config.json', {'model': 'same-model', 'model_parameters': parameters})
            (root / 'trace').mkdir()
            records = [{'boundary': 'http', 'call_id': str(i), 'event': 'started',
                        'requested_model': 'same-model', 'request_schema_and_sampling': copy.deepcopy(parameters)}
                       for i in range(2)]
            trace = root / 'trace/http.jsonl'
            def save():
                trace.write_text(''.join(json.dumps(r) + '\n' for r in records))
            save()
            self.assertTrue(audit_trace(root, 'shared', 'opening')['configured_model_parameters_match_requests'])
            records[1]['request_schema_and_sampling'] = {}
            save()
            self.assertFalse(audit_trace(root, 'shared', 'opening')['configured_model_parameters_match_requests'])

    def test_partial_non_utf8_response_archives_bytes_without_success_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'trace').mkdir()
            partial = b'data: {"content":"\xe4\xb8'
            (root / 'trace/response.partial.bin').write_bytes(partial)
            record = {'boundary': 'http', 'call_id': 'cut-off', 'event': 'error',
                      'delivery_status': 'delivery_unknown', 'response_complete': False,
                      'response_file': 'response.partial.bin', 'usage': None}
            (root / 'trace/http.jsonl').write_text(json.dumps(record) + '\n')
            audit = audit_trace(root, 'shared', 'opening', materialize=True)
            self.assertEqual((root / 'responses/cut-off.bin').read_bytes(), partial)
            self.assertFalse(audit['usage_coverage_complete'])
            self.assertEqual(audit['delivery_unknown_calls'], ['cut-off'])
            self.assertEqual(audit['missing_response_files'], [])


if __name__ == '__main__':
    unittest.main()

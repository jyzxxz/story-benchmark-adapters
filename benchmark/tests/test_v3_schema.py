"""The public JSON Schema validates recorded v3 envelopes, not story quality."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from story_benchmark.compiler import compile_case
from story_benchmark.result import RESULT_KEYS, empty_result, validate_result
from story_benchmark.runner import execute_run
from test_v3 import Fixture


ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = ('if_line', 'ai4visualnovel', 'infiplot')


@unittest.skipIf(Draft202012Validator is None, 'optional jsonschema dependency unavailable')
class V3SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / 'schemas/result-v3.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('cases', 'profiles'):
            shutil.copytree(ROOT / name, self.root / name)
        self.bundle = self.root / 'bundle'
        compile_case(self.root / 'cases/CAMPUS-01-V3.json', self.bundle, True)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, system='if_line', mode='normal'):
        return execute_run(system, self.bundle, {'model': 'frozen'},
                           self.root / (system + '-' + mode), Fixture(system, mode), mock=True)

    def assert_schema_valid(self, value):
        errors = sorted(self.validator.iter_errors(value), key=lambda error: str(list(error.path)))
        self.assertEqual(errors, [], '\n'.join(f'{list(e.path)}: {e.message}' for e in errors))
        validate_result(value)

    def test_schema_matches_exact_executable_envelope(self):
        self.assertEqual(self.schema['$schema'], 'https://json-schema.org/draft/2020-12/schema')
        self.assertEqual(set(self.schema['required']), RESULT_KEYS)
        self.assertEqual(set(self.schema['properties']), RESULT_KEYS)
        self.assertFalse(self.schema['additionalProperties'])
        self.assertEqual(len(RESULT_KEYS), 14)
        self.assert_schema_valid(empty_result('if_line', 'not-started'))

    def test_all_three_success_results_allow_empty_current_body(self):
        for system in SYSTEMS:
            with self.subTest(system=system):
                result = self.fixture(system)
                self.assertEqual(result['outcome'], 'completed')
                self.assertEqual(result['content']['body'], [])
                self.assertIs(result['scope']['selection_executed'], False)
                self.assert_schema_valid(result)

    def test_all_three_native_failures_preserve_null_selection(self):
        for system in SYSTEMS:
            with self.subTest(system=system):
                result = self.fixture(system, 'native_failure')
                self.assertEqual(result['outcome'], 'native_error')
                self.assertIsNone(result['scope']['selection_executed'])
                before = copy.deepcopy(result)
                self.assert_schema_valid(result)
                self.assertEqual(result, before)  # Validation must not fill unknowns with false.

    def test_all_three_input_rejections_keep_same_shape(self):
        for system in SYSTEMS:
            with self.subTest(system=system):
                self.fixture(system)
                # Reject a reused run directory before prepare/generation. This
                # is the actual public failure response, not a hand-built one.
                result = execute_run(system, self.bundle, {}, self.root / (system + '-normal'),
                                     Fixture(system), mock=True)
                self.assertEqual(result['outcome'], 'input_rejected')
                self.assertIsNone(result['scope']['selection_executed'])
                self.assert_schema_valid(result)

    def test_success_requires_observed_unselected_boundary(self):
        result = self.fixture()
        for value in (None, True, 0, 'false'):
            with self.subTest(selection=value):
                altered = copy.deepcopy(result)
                altered['scope']['selection_executed'] = value
                self.assertFalse(self.validator.is_valid(altered))
        altered = copy.deepcopy(result)
        altered['scope']['status'] = 'not_evaluated'
        self.assertFalse(self.validator.is_valid(altered))

    def test_missing_extra_or_wrong_type_fields_are_rejected(self):
        result = self.fixture()
        for key in RESULT_KEYS:
            with self.subTest(missing=key):
                altered = copy.deepcopy(result)
                del altered[key]
                self.assertFalse(self.validator.is_valid(altered))
        for container in (None, 'input', 'scope', 'content', 'usage', 'artifacts', 'provenance'):
            with self.subTest(extra_in=container):
                altered = copy.deepcopy(result)
                (altered if container is None else altered[container])['invented'] = None
                self.assertFalse(self.validator.is_valid(altered))
        altered = copy.deepcopy(result)
        altered['usage']['prompt_tokens'] = 'unknown'
        self.assertFalse(self.validator.is_valid(altered))

    def test_source_pointer_accepts_json_pointer_or_positive_line_object(self):
        result = self.fixture(mode='body')
        for pointer in ('/paragraphs/0/text', {'line': 1}):
            with self.subTest(pointer=pointer):
                altered = copy.deepcopy(result)
                altered['content']['body'][0]['source']['pointer'] = pointer
                self.assert_schema_valid(altered)
        for pointer in ('line:1', '', None, {'line': 0}, {'line': True}, {'line': '1'}):
            with self.subTest(invalid_pointer=pointer):
                altered = copy.deepcopy(result)
                altered['content']['body'][0]['source']['pointer'] = pointer
                self.assertFalse(self.validator.is_valid(altered))
        for source_file in ('/absolute.json', '../outside.json', 'native/../outside.json'):
            with self.subTest(invalid_source=source_file):
                altered = copy.deepcopy(result)
                altered['content']['body'][0]['source']['file'] = source_file
                self.assertFalse(self.validator.is_valid(altered))


if __name__ == '__main__':
    unittest.main()

import copy
import json
from pathlib import Path
import tempfile
import unittest

from story_benchmark.compiler import compile_case, validate_output_boundary, verify_bundle
from story_benchmark.io import BenchmarkError, atomic_json, sha256
from story_benchmark.output_boundary import audit_output_boundary
from story_benchmark.runner import run_once, verify_saved_run
from test_core import RecordedFixtureAdapter

ROOT = Path(__file__).resolve().parents[1]


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)
        self.case = json.loads((ROOT/'cases/CAMPUS-01-C1.json').read_text())
        self.labels = [o['text'] for o in self.case['decisions'][0]['options']]
        atomic_json(self.run/'choices.json', self.labels)
        self.export = {
            'choices': [{'label': label, 'native_source': 'choices.json', 'native_pointer': '/'+str(i)}
                        for i, label in enumerate(self.labels)],
            'stop_reason': 'first_choice', 'selection_executed': False,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_unexecuted_native_choice_boundary(self):
        report = audit_output_boundary(self.run, self.case, self.export)
        self.assertTrue(report['technical_boundary_passed'])
        self.assertTrue(report['exact_choice_labels_match'])

    def test_rejects_selected_missing_forged_and_malformed_choices(self):
        cases = []
        selected = copy.deepcopy(self.export); selected['selection_executed'] = True; cases.append(selected)
        missing = copy.deepcopy(self.export); missing['choices'].pop(); cases.append(missing)
        forged = copy.deepcopy(self.export); forged['choices'][0]['label'] = 'invented'; cases.append(forged)
        malformed = copy.deepcopy(self.export); malformed['choices'][0]['label'] = {}; cases.append(malformed)
        for export in cases:
            with self.subTest(export=export):
                self.assertFalse(audit_output_boundary(self.run, self.case, export)['technical_boundary_passed'])

    def test_paraphrase_requires_content_review(self):
        self.export['choices'][0]['label'] = 'native paraphrase'
        atomic_json(self.run/'choices.json', ['native paraphrase', self.labels[1]])
        report = audit_output_boundary(self.run, self.case, self.export)
        self.assertTrue(report['technical_boundary_passed'])
        self.assertFalse(report['exact_choice_labels_match'])
        self.assertIn('not_evaluated', report['semantic_equivalence'])

    def test_future_preview_cannot_become_choice_label(self):
        self.export['choices'] = [{'preview_text': 'future A'}, {'preview_text': 'future B'}]
        self.export['stop_reason'] = 'unselected_candidates'
        self.assertFalse(audit_output_boundary(self.run, self.case, self.export)['technical_boundary_passed'])

    def test_contract_cannot_execute_or_skip_first_choice(self):
        for patch in ({'execute_choice': True}, {'decision_id': 'C2'}, {'include_options': False}):
            case = copy.deepcopy(self.case); case['output_boundary'].update(patch)
            with self.subTest(patch=patch), self.assertRaises(BenchmarkError):
                validate_output_boundary(case)

    def test_new_bundle_preserves_old_brief_opening_and_old_bundle_hash(self):
        compile_case(ROOT/'cases/CAMPUS-01-C1.json', self.run/'new', True)
        verify_bundle(self.run/'new')
        self.assertEqual((ROOT/'cases/CAMPUS-01.opening.txt').read_bytes(), (self.run/'new/opening.txt').read_bytes())
        self.assertIn((ROOT/'cases/CAMPUS-01.brief.txt').read_text(), (self.run/'new/shared_task.txt').read_text())
        compile_case(ROOT/'cases/CAMPUS-01.json', self.run/'old', True)
        self.assertEqual(sha256((self.run/'old/shared_task.txt').read_bytes()),
                         'af81207f820c2e6e8ea56939e4b4ed313f1810bf089068bfa1853549c440302a')

    def test_sealed_candidate_evidence_never_claims_visible_story(self):
        class PreviewFixture(RecordedFixtureAdapter):
            def export_first_artifact(self, handle):
                exported = super().export_first_artifact(handle)
                return {'segments': [], 'choices': [], 'unselected_previews': exported['segments'],
                        'selection_executed': False, 'stop_reason': 'unselected_candidates',
                        'native_capability_status': 'unsupported_output_boundary'}
        bundle = self.run/'bundle'
        compile_case(ROOT/'cases/CAMPUS-01-C1.json', bundle, True)
        run = self.run/'fixture'
        result = run_once('if_line', bundle, {'model': 'frozen'}, run, PreviewFixture(), mock=True)
        self.assertEqual(result['adapter_status'], 'completed')
        self.assertEqual(result['generation_status'], 'unsupported_output_boundary')
        self.assertEqual(result['native_integration'], 'verified_with_fixture_provider')
        self.assertEqual((run/'export/generated.jsonl').read_text(), '')
        self.assertTrue((run/'export/unselected_previews.jsonl').read_text())
        self.assertEqual(json.loads((run/'case.json').read_text()), self.case)
        self.assertEqual(verify_saved_run(run), result)


if __name__ == '__main__':
    unittest.main()

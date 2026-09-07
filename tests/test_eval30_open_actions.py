"""Offline input/contract regression. Does not start native services or models."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import eval30
from story_benchmark.compiler import compile_case, load_case, render, verify_bundle
from story_benchmark.contracts import native_decisions, validate_contracts
from story_benchmark.open_actions import RULES, expand_suite, revise_brief
from story_benchmark.io import atomic_json, atomic_write, read_json, sha256


class OpenActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.open_root = cls.root/'open'
        cls.legacy_root = cls.root/'legacy'
        cls.manifest = eval30.expand_suite(cls.open_root)
        cls.legacy = eval30.expand_legacy_suite(cls.legacy_root)
        cls.catalog, cls.briefs, _ = eval30.catalog_sources()
        cls.row = cls.manifest['cases'][0]
        cls.case = read_json(cls.open_root/cls.row['case_file'])
        cls.legacy_case = read_json(cls.legacy_root/cls.legacy['cases'][0]['case_file'])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_default_exports_open_policy(self):
        self.assertEqual(self.manifest['decision_policy'], 'native_generated')
        self.assertEqual(len(self.manifest['cases']), 30)

    def test_all_cases_have_no_prescribed_actions(self):
        for row in self.manifest['cases']:
            c = read_json(self.open_root/row['case_file'])
            self.assertEqual(c['decisions'], [])
            self.assertTrue(native_decisions(c))
            self.assertEqual(c['review_status'], 'pilot')

    def test_legacy_shared_hashes_unchanged(self):
        for row in self.catalog['cases']:
            check = verify_bundle(self.legacy_root/'compiled'/row['case_id'])
            self.assertEqual(check['shared_sha256'], row['shared_sha256'])

    def test_legacy_briefs_verbatim(self):
        for ident, brief in self.briefs.items():
            self.assertEqual((self.legacy_root/'briefs'/f'{ident}.txt').read_bytes(), brief.encode())

    def test_titles_casts_and_visuals_unchanged(self):
        old = {r['source_prompt_id']: r for r in self.catalog['cases']}
        for row in self.manifest['cases']:
            c = read_json(self.open_root/row['case_file'])
            for key in ('title', 'player_name', 'character_names', 'visual_style'):
                self.assertEqual(c[key], old[row['source_prompt_id']][key])

    def test_all_openings_byte_identical(self):
        for row in self.catalog['cases']:
            self.assertEqual((self.open_root/'openings'/f'{row["source_prompt_id"]}.txt').read_bytes(), row['opening'].encode())

    def test_all_three_payloads_equal(self):
        for row in self.manifest['cases']:
            b = self.open_root/'compiled'/row['case_id']
            check = verify_bundle(b)
            shared = (b/'shared_task.txt').read_text()
            self.assertEqual(check['shared_sha256'], row['shared_sha256'])
            self.assertEqual(read_json(b/'payloads/if_line.json')['extra_requirements'], shared)
            self.assertEqual(read_json(b/'payloads/infiplot.json')['worldSetting'], shared)
            self.assertEqual((b/'payloads/requirements.txt').read_text(), shared)

    def test_no_legacy_action_or_chapter_bindings_in_active_input(self):
        for row in self.manifest['cases']:
            shared = (self.open_root/'compiled'/row['case_id']/'shared_task.txt').read_text()
            self.assertIsNone(re.search(r'C[12]|第[一二]次选择：|第[一二三四五0-9]+章|前两章|第三叙事阶段', shared))
            self.assertNotIn('保留原题两组关键选择', shared)
            self.assertIn('不预设关键行动', shared)

    def test_actual_chosen_actions_still_must_be_respected(self):
        shared = (self.open_root/'compiled'/self.row['case_id']/'shared_task.txt').read_text()
        self.assertIn('提交选择后须承接已执行行动', shared)
        self.assertIn('未选路线', shared)

    def test_new_hashes_differ_and_old_hashes_traceable(self):
        old = {r['source_prompt_id']: r for r in self.catalog['cases']}
        for row in self.manifest['cases']:
            c = read_json(self.open_root/row['case_file'])
            self.assertNotEqual(row['shared_sha256'], old[row['source_prompt_id']]['shared_sha256'])
            self.assertEqual(c['provenance']['parent_shared_sha256'], old[row['source_prompt_id']]['shared_sha256'])

    def test_prescribed_actions_archived_not_active(self):
        log = read_json(self.open_root/'CHANGELOG.json')
        self.assertEqual(len(log['cases']), 30)
        for change in log['cases']:
            removed = [r for r in change['brief_changes'] if r['after'] == '']
            self.assertEqual(len(removed), 2)
            self.assertIn('C1', json.dumps(change['old_scope'], ensure_ascii=False))
        self.assertEqual((self.open_root/'history/original_v1.md').read_bytes(),
                         (ROOT/'benchmark/source/if_line_eval_prompts_30.v1.md').read_bytes())

    def test_zero_model_calls_claim_only_compilation(self):
        self.assertEqual(self.manifest['paid_model_calls'], 0)
        self.assertEqual(self.manifest['native_integration'], 'not_run')

    def test_window_and_contract_unchanged(self):
        self.assertEqual(self.case['output_contract'], self.legacy_case['output_contract'])
        self.assertEqual(self.case['input_contract'], self.legacy_case['input_contract'])

    def test_unknown_policy_rejected(self):
        c = copy.deepcopy(self.case); c['decision_policy'] = 'guess'
        with self.assertRaises(ValueError): validate_contracts(c)

    def test_null_policy_rejected(self):
        c = copy.deepcopy(self.case); c['decision_policy'] = None
        with self.assertRaises(ValueError): validate_contracts(c)

    def test_open_policy_with_prescribed_choices_rejected(self):
        c = copy.deepcopy(self.case); c['decisions'] = self.legacy_case['decisions']
        with self.assertRaises(ValueError): validate_contracts(c)

    def test_open_policy_rejects_non_list_empty(self):
        for val in (None, {}, '', ()):
            c = copy.deepcopy(self.case); c['decisions'] = val
            with self.assertRaises(ValueError): validate_contracts(c)

    def test_legacy_case_still_requires_two_choices(self):
        c = copy.deepcopy(self.legacy_case); c['decisions'] = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root/'cases/test.json'; atomic_json(path, c)
            with self.assertRaisesRegex(ValueError, 'two_decisions_required'):
                load_case(path, allow_pilot=True)

    def test_open_policy_not_accepted_for_v3_boundary(self):
        c = copy.deepcopy(self.case); c['output_contract'] = {'version': '3.0'}
        with self.assertRaises(ValueError): validate_contracts(c)

    def test_open_policy_rejects_first_choice_boundary(self):
        c = copy.deepcopy(self.case); c['output_boundary'] = {}
        with self.assertRaises(ValueError): validate_contracts(c)

    def test_pilot_cannot_be_formal_without_review(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                compile_case(self.open_root/self.row['case_file'], Path(folder)/'out', allow_pilot=False)

    def test_approved_without_review_record_rejected(self):
        c = copy.deepcopy(self.case); c['review_status'] = 'approved'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for key in ('brief', 'opening', 'prefix'):
                atomic_write(root/c[key+'_file'], (self.open_root/c[key+'_file']).read_bytes())
            path = root/'cases/test.json'; atomic_json(path, c)
            with self.assertRaisesRegex(ValueError, 'approval_record_required'):
                compile_case(path, root/'out')

    def test_fixture_approval_retains_open_version_and_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); approved = root/'approved'
            result = subprocess.run([sys.executable, str(ROOT/'tools/approve_eval30.py'),
                '--suite-root', str(self.open_root), '--out', str(approved),
                '--reviewer', 'test-only-fixture', '--confirm-reviewed'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            c = read_json(approved/self.row['case_file'])
            self.assertIn('open-actions-approved', c['case_version'])
            self.assertTrue(native_decisions(c))
            compile_case(approved/self.row['case_file'], root/'compiled')
            c['decision_policy'] = 'changed'; atomic_json(approved/self.row['case_file'], c)
            with self.assertRaises(ValueError): compile_case(approved/self.row['case_file'], root/'tampered')

    def test_no_overwrite(self):
        with self.assertRaises(FileExistsError): expand_suite(self.open_root)

    def test_replacements_require_exact_source_match(self):
        with self.assertRaises(ValueError): revise_brief(self.briefs['CAMPUS-01'], [['absent', 'new']])

    def test_prefix_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); rules = read_json(RULES)
            atomic_write(root/rules['prefix_file'], 'tampered')
            atomic_json(root/'rules.json', rules)
            with self.assertRaisesRegex(ValueError, 'prefix hash mismatch'):
                expand_suite(root/'out', root/'rules.json')

    def test_changes_not_in_runtime_bundle(self):
        for row in self.manifest['cases']:
            b = self.open_root/'compiled'/row['case_id']
            self.assertFalse((b/'CHANGELOG.json').exists())
            self.assertFalse((b/'history').exists())

    def test_source_policy_tamper_rejected(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            b = Path(folder)/'bundle'
            shutil.copytree(self.open_root/'compiled'/self.row['case_id'], b)
            c = read_json(b/'case.json'); c.pop('decision_policy'); atomic_json(b/'case.json', c)
            with self.assertRaises(ValueError): verify_bundle(b)


if __name__ == '__main__':
    unittest.main()

"""Action-open input/approval/runner regressions. No paid or native model calls."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import eval30 as legacy
import open_eval30 as opened
import experiment as exp
from story_benchmark.compiler import compile_case, load_case, verify_bundle
from story_benchmark.io import read_json, atomic_json, sha256
from story_benchmark.contracts import validate_contracts


class OpenSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.suite = Path(cls.temp.name)/'suite'
        cls.manifest = opened.expand_suite(cls.suite)
        cls.catalog, cls.briefs, cls.prefix = opened.catalog_sources()
        cls.old, cls.old_briefs, _ = legacy.catalog_sources()
        cls.case = read_json(cls.suite/cls.manifest['cases'][0]['case_file'])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_30_have_real_empty_prescribed_actions(self):
        self.assertEqual(len(self.manifest['cases']), 30)
        for row in self.manifest['cases']:
            case = read_json(self.suite/row['case_file'])
            self.assertEqual(case['decision_policy'], 'native_generated')
            self.assertEqual(case['decisions'], [])
            self.assertEqual(case['review_status'], 'pilot')
            self.assertEqual(case['case_version'], '4.1-open-actions-pilot.2')

    def test_same_30_topics_and_casts_not_a_new_example_story(self):
        keys = ('source_prompt_id', 'title', 'genre', 'player_name', 'character_names')
        for old, new in zip(self.old['cases'], self.catalog['cases']):
            self.assertEqual({k:old[k] for k in keys}, {k:new[k] for k in keys})

    def test_original_opening_except_final_paragraph_unchanged(self):
        for old, new in zip(self.old['cases'], self.catalog['cases']):
            expected = old['opening'].rsplit('\n\n', 1)[0]
            if old['source_prompt_id'] == 'SCI-FI-03':
                expected = expected.replace('面前是一项尚未确认的选择：交出一段记忆，或拒绝本次征收。', '此刻尚未回应本次征收。')
            self.assertEqual(expected, new['opening'].rsplit('\n\n', 1)[0])

    def test_no_predefined_menu_or_chapter_bindings_in_active_inputs(self):
        forbidden = r'C[12]|第[一二]次选择[：:]|第[一二三]章|前两章|保留原题两组关键选择|面前是一项尚未确认的选择[：:]'
        for row in self.manifest['cases']:
            text = (self.suite/'compiled'/row['case_id']/'shared_task.txt').read_text()
            self.assertIsNone(re.search(forbidden, text), row['source_prompt_id'])
            self.assertIn('本任务不提供指定行动清单', text)
            self.assertNotIn('观察范围之内仍应呈现题目要求的选择和后果', text)

    def test_three_payloads_equal_and_hashes_frozen(self):
        for row in self.manifest['cases']:
            bundle = self.suite/'compiled'/row['case_id']
            result = verify_bundle(bundle)
            self.assertEqual(result['shared_sha256'], self.catalog['expected_shared_sha256'][row['source_prompt_id']])
            shared = (bundle/'shared_task.txt').read_text()
            self.assertEqual(read_json(bundle/'payloads/if_line.json')['extra_requirements'], shared)
            self.assertEqual(read_json(bundle/'payloads/infiplot.json')['worldSetting'], shared)
            self.assertEqual((bundle/'payloads/requirements.txt').read_text(), shared)

    def test_output_and_character_contracts_not_relaxed(self):
        self.assertEqual(self.case['input_contract']['character_policy'], 'exact_declared_cast')
        self.assertEqual(self.case['input_contract']['version'], '3.0')
        self.assertEqual(self.case['output_contract'], self.old['common_case']['output_contract'])

    def test_complete_readable_export_and_no_generation_claim(self):
        text = (self.suite/'PROMPTS_30_COMPILED.md').read_text()
        self.assertEqual(text.count('<<<SHARED_TASK_V2_BEGIN>>>'), 30)
        self.assertEqual(self.manifest['paid_model_calls'], 0)
        self.assertEqual(self.manifest['native_integration'], 'not_run')

    def test_refuses_overwrite(self):
        with self.assertRaises(FileExistsError):
            opened.expand_suite(self.suite)

    def test_source_file_tampering_rejected(self):
        for file in ('PROMPTS.md', 'prefix.txt', 'CHANGES.md'):
            with self.subTest(file=file), tempfile.TemporaryDirectory() as folder:
                dest = Path(folder)/'sources'
                shutil.copytree(opened.CATALOG.parent, dest)
                with (dest/file).open('a') as stream:
                    stream.write('changed')
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    opened.catalog_sources(dest/'catalog.json')

    def test_source_residue_rejected_even_after_rehash(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)/'sources'; shutil.copytree(opened.CATALOG.parent, dest)
            source = dest/'PROMPTS.md'
            source.write_text(source.read_text().replace('标题：雨夜信号', '标题：雨夜信号\n第一次选择：固定行动', 1))
            catalog = read_json(dest/'catalog.json')
            catalog['file_sha256']['PROMPTS.md'] = sha256(source.read_bytes())
            atomic_json(dest/'catalog.json', catalog)
            with self.assertRaisesRegex(ValueError, 'residue'):
                opened.catalog_sources(dest/'catalog.json')

    def test_wrong_expected_compiled_hash_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); dest = folder/'sources'; shutil.copytree(opened.CATALOG.parent, dest)
            catalog = read_json(dest/'catalog.json')
            catalog['expected_shared_sha256']['CAMPUS-01'] = '0'*64
            atomic_json(dest/'catalog.json', catalog)
            with self.assertRaisesRegex(ValueError, 'Frozen open task changed'):
                opened.expand_suite(folder/'output', dest/'catalog.json')

    def test_new_and_legacy_hashes_are_distinct(self):
        old = {r['source_prompt_id']:r['shared_sha256'] for r in self.old['cases']}
        self.assertTrue(all(r['shared_sha256'] != old[r['source_prompt_id']] for r in self.manifest['cases']))

    def test_manifest_policy_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)/'bundle'
            shutil.copytree(self.suite/'compiled'/self.case['case_id'], dest)
            manifest = read_json(dest/'manifest.json'); manifest['decision_policy'] = 'specified'
            atomic_json(dest/'manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'decision_policy'):
                verify_bundle(dest)

    def test_no_policy_does_not_silently_enable_empty_decisions(self):
        case = copy.deepcopy(self.case); case.pop('decision_policy')
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'cases/test.json'; atomic_json(p,case)
            with self.assertRaisesRegex(ValueError, 'two_decisions_required'):
                load_case(p, allow_pilot=True)

    def test_native_policy_rejects_nonempty_or_fake_decisions(self):
        for value in (self.old['cases'][0]['decisions'], None, {}, '', ['native-generated']):
            case = copy.deepcopy(self.case); case['decisions'] = value
            with self.assertRaises(ValueError):
                validate_contracts(case)

    def test_unknown_policy_rejected(self):
        for value in ('open', '', None, True, {}):
            case = copy.deepcopy(self.case); case['decision_policy'] = value
            with self.assertRaises(ValueError):
                validate_contracts(case)

    def test_native_policy_requires_full_v4_readable_window(self):
        for key, value in (('profile', 'TEXT_CONTINUATION_DEV'), ('output_boundary', {}),
                           ('output_contract', {'version':'3.0'}), ('input_contract', {})):
            case = copy.deepcopy(self.case); case[key] = value
            with self.assertRaises(ValueError):
                validate_contracts(case)

    def test_absent_policy_keeps_legacy_checks(self):
        case = copy.deepcopy(self.case)
        case.pop('decision_policy')
        case['decisions'] = copy.deepcopy(self.old['cases'][0]['decisions'])
        validate_contracts(case)
        case['decisions'][0]['options'].pop()
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'cases/test.json'; atomic_json(p,case)
            with self.assertRaisesRegex(ValueError,'invalid_options'):
                load_case(p, allow_pilot=True)

    def test_native_policy_never_means_approved(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                compile_case(self.suite/self.manifest['cases'][0]['case_file'], Path(folder)/'bundle', allow_pilot=False)

    def test_open_approval_preserves_version_and_has_valid_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); dest = folder/'approved'
            cmd = [sys.executable, str(ROOT/'tools/approve_eval30.py'), '--suite-root', str(self.suite),
                   '--out', str(dest), '--reviewer', 'test-fixture-reviewer', '--confirm-reviewed']
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read_json(dest/'suite_manifest.json')['suite_version'], 'v4-30-open-actions-approved.2')
            for row in self.manifest['cases']:
                case = read_json(dest/row['case_file'])
                self.assertEqual(case['case_version'], '4.1-open-actions-approved.2')
                self.assertEqual(case['decisions'], [])
                compile_case(dest/row['case_file'], folder/'compiled'/row['case_id'], allow_pilot=False)
            self.assertEqual(read_json(self.suite/self.manifest['cases'][0]['case_file'])['review_status'], 'pilot')

    def test_approval_rejects_changed_export_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); source = folder/'suite'
            shutil.copytree(self.suite, source, ignore=shutil.ignore_patterns('compiled'))
            manifest = read_json(source/'suite_manifest.json')
            manifest['cases'][0]['shared_sha256'] = '0'*64
            atomic_json(source/'suite_manifest.json', manifest)
            result = subprocess.run([sys.executable, str(ROOT/'tools/approve_eval30.py'), '--suite-root', str(source),
                                     '--out', str(folder/'approved'), '--reviewer', 'test-fixture-reviewer', '--confirm-reviewed'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((folder/'approved').exists())

    def test_approval_rejects_missing_human_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(ROOT/'tools/approve_eval30.py'), '--suite-root', str(self.suite),
                                     '--out', str(Path(folder)/'approved'), '--reviewer', 'test-fixture-reviewer'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(folder)/'approved').exists())


class OpenRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.config = self.work/'config.json'
        atomic_json(self.config, {'schema_version':'batch.1', 'evidence_kind':'live', 'allow_pilot':True,
            'providers':{role:{'model':'fixture', 'base_url':'https://example.invalid/v1', 'api_key_env':'TEST_'+role.upper()}
                         for role in ('text','image','vision')},
            'systems':{s:{'repo_path':'systems/'+s} for s in exp.SYSTEMS}, 'bundles':['unused']})
        self.args = argparse.Namespace(preset='pilot', case_ids=['CAMPUS-01','SCI-FI-01'], genres=None,
            count=7, repeat=None, systems=list(exp.SYSTEMS), choices=[0,1], concurrency=2,
            config=self.config, secrets=self.work/'secrets.env', suite_root=None, out=self.work/'out',
            allow_pilot=True, yes=True, run=False, resume=False, verify=False, preflight=False)

    def test_default_runner_freezes_new_suite_and_action_policy(self):
        out, plan = exp.prepare(self.args)
        self.assertEqual(plan['decision_policy'], 'native_generated')
        self.assertEqual(plan['suite_version'], 'v4-30-open-actions-pilot.2')
        self.assertEqual(plan['total_attempts'], 21)
        self.assertEqual(plan['attempts_by_case'], {'CAMPUS-01':4, 'SCI-FI-01':3})
        for bundle in read_json(out/'config.json')['bundles']:
            self.assertEqual(read_json(Path(bundle)/'case.json')['decisions'], [])

    def test_new_exporter_is_in_code_freeze(self):
        self.assertIn('tools/open_eval30.py', exp.code_inventory())

    def test_legacy_suite_still_runs_only_when_explicitly_selected(self):
        source = self.work/'legacy'; legacy.expand_legacy_suite(source)
        self.args.suite_root = source
        _, plan = exp.prepare(self.args)
        self.assertEqual(plan['decision_policy'], 'specified')
        self.assertEqual(plan['suite_version'], 'v4-30-pilot.1')

    def test_mixed_action_policies_rejected(self):
        source = self.work/'mixed'; manifest = opened.expand_suite(source)
        old_source = self.work/'legacy'; old_manifest = legacy.expand_legacy_suite(old_source)
        first = old_manifest['cases'][0]
        case = read_json(old_source/first['case_file'])
        # Preserve two valid cases of different policies, not an invalid placeholder.
        for key in ('brief', 'opening', 'prefix'):
            old_file = old_source/case[key+'_file']
            new_relative = 'legacy_sources/'+key+'.txt'
            target = source/new_relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(old_file.read_bytes()); case[key+'_file'] = new_relative
        atomic_json(source/first['case_file'], case)
        manifest['cases'][0] = first
        atomic_json(source/'suite_manifest.json', manifest)
        self.args.suite_root = source
        with self.assertRaisesRegex(ValueError, 'Do not mix prescribed-action'):
            exp.prepare(self.args)

    def test_compatibility_exporter_has_same_current_default(self):
        root = self.work/'compat'
        manifest = legacy.expand_suite(root)
        self.assertEqual(manifest['suite_version'], 'v4-30-open-actions-pilot.2')
        self.assertTrue(all(r['case_id'].endswith('OPEN02') for r in manifest['cases']))

    def test_explicit_first_open_revision_is_retained(self):
        root = self.work/'open-v1'
        result = subprocess.run([sys.executable, str(ROOT/'tools/eval30.py'), '--open-actions-v1', '--out', str(root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(root/'suite_manifest.json')['suite_version'], 'v4-30-open-actions-pilot.1')
        self.assertTrue(all(r['case_id'].endswith('OPEN01') for r in read_json(root/'suite_manifest.json')['cases']))

    def test_root_preview_selects_action_open_inputs(self):
        out = self.work/'preview'
        result = subprocess.run(['bash', str(ROOT/'experiment.sh'), 'preview', '--out', str(out)],
                                cwd='/tmp', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(out/'suite_manifest.json')['decision_policy'], 'native_generated')
        self.assertFalse((out/'if_line').exists())

    def test_root_preview_legacy_retains_old_hashes(self):
        out = self.work/'legacy'
        result = subprocess.run(['bash', str(ROOT/'experiment.sh'), 'preview-legacy', '--out', str(out)],
                                cwd='/tmp', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(out/'suite_manifest.json')['suite_version'], 'v4-30-pilot.1')

    def test_prepare_does_not_call_native_or_paid_services(self):
        with patch.object(exp, 'call', side_effect=AssertionError('No native calls')):
            out, plan = exp.prepare(self.args)
        self.assertFalse((out/'if_line').exists())
        self.assertEqual(exp.read_plan(out)['decision_policy'], plan['decision_policy'])


if __name__ == '__main__':
    unittest.main()

"""Read-only open-input audit tests using real compiler-built local fixtures."""
from contextlib import redirect_stdout, redirect_stderr
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from audit_open_inputs import audit, main
from story_benchmark.compiler import compile_case
from story_benchmark.contracts import INPUT_CONTRACT
from story_benchmark.io import atomic_json, atomic_write, read_json, sha256


class OpenInputAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / 'suite'
        texts = {'brief': '标题：测试\n角色：甲。初始地点是一间房间。',
                 'opening': '甲站在窗前，还没有决定下一步行动。',
                 'prefix': '继续创作图文故事，具体行动由原生系统设计。'}
        self.case = {
            'case_id': 'TEST-01', 'case_version': '4.0-open-actions-pilot.test',
            'review_status': 'pilot', 'title': '测试', 'language': 'zh-CN',
            'player_name': '甲', 'character_names': ['甲'], 'visual_style': '自然光',
            'profile': 'FULL_VN', 'scope_map': {'初始状态': '前文已经发生，后续行动未定。'},
            'decisions': [], 'decision_policy': 'native_generated',
            'input_contract': copy.deepcopy(INPUT_CONTRACT),
            'output_contract': {'version': '4.0', 'scope': 'readable_window', 'window_chars': 4000,
                'character_metric': 'unicode_codepoints_in_new_visible_body',
                'cut_rule': 'first_sentence_boundary_at_or_after_threshold', 'media': 'images',
                'native_choice_previews': 'separate', 'require_story_ending': False},
            'provenance': {'source_sha256': {k: sha256(v) for k, v in texts.items()}},
        }
        for key, text in texts.items():
            self.case[key + '_file'] = 'sources/' + key + '.txt'
            atomic_write(self.root / self.case[key + '_file'], text)
        self.source = self.root / 'cases/TEST-01.json'
        atomic_json(self.source, self.case)
        self.bundle = self.root / 'compiled/TEST-01'
        receipt = compile_case(self.source, self.bundle, allow_pilot=True)
        self.catalog = {'suite_version': 'fixture-open-pilot.test', 'case_count': 1,
            'decision_policy': 'native_generated', 'cases': [{
                'source_prompt_id': 'TEST-01', 'case_id': 'TEST-01', 'case_file': 'cases/TEST-01.json',
                'decision_policy': 'native_generated', 'review_status': 'pilot',
                'shared_sha256': receipt['shared_sha256']}]}
        self.save_catalog()

    def save_catalog(self):
        atomic_json(self.root / 'suite_manifest.json', self.catalog)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_real_compiled_open_fixture_passes(self):
        self.assertTrue(audit(self.root)['ok'])

    def test_audit_does_not_modify_inputs(self):
        before = self.snapshot(); audit(self.root); self.assertEqual(before, self.snapshot())

    def test_missing_case_count_rejected(self):
        del self.catalog['case_count']; self.save_catalog()
        with self.assertRaises(ValueError): audit(self.root)

    def test_incorrect_count_rejected(self):
        self.catalog['case_count'] = 2; self.save_catalog()
        with self.assertRaises(ValueError): audit(self.root)

    def test_duplicate_ids_rejected(self):
        self.catalog['cases'] *= 2; self.catalog['case_count'] = 2; self.save_catalog()
        with self.assertRaises(ValueError): audit(self.root)

    def test_empty_catalog_rejected(self):
        self.catalog['cases'] = []; self.catalog['case_count'] = 0; self.save_catalog()
        with self.assertRaises(ValueError): audit(self.root)

    def test_legacy_policy_rejected(self):
        self.catalog['decision_policy'] = 'prescribed'; self.save_catalog()
        with self.assertRaises(ValueError): audit(self.root)

    def test_unsafe_identifier_reported(self):
        self.catalog['cases'][0]['case_id'] = '../outside'; self.save_catalog()
        self.assertFalse(audit(self.root)['ok'])

    def test_case_path_escape_reported(self):
        self.catalog['cases'][0]['case_file'] = '../outside.json'; self.save_catalog()
        self.assertFalse(audit(self.root)['ok'])

    def test_source_tamper_reported(self):
        atomic_write(self.root / self.case['brief_file'], 'tampered')
        self.assertFalse(audit(self.root)['ok'])

    def test_payload_tamper_reported(self):
        atomic_write(self.bundle / 'payloads/requirements.txt', 'tampered')
        self.assertFalse(audit(self.root)['ok'])

    def test_catalog_hash_mismatch_reported(self):
        self.catalog['cases'][0]['shared_sha256'] = '0' * 64; self.save_catalog()
        self.assertFalse(audit(self.root)['ok'])

    def test_review_status_mismatch_reported(self):
        self.catalog['cases'][0]['review_status'] = 'approved'; self.save_catalog()
        self.assertFalse(audit(self.root)['ok'])

    def test_pilot_rejected_in_formal_mode(self):
        self.assertFalse(audit(self.root, formal_only=True)['ok'])

    def test_valid_fixture_approval_passes_formal_mode(self):
        import shutil
        shutil.rmtree(self.bundle)
        self.case.update(review_status='approved', review_file='reviews/test.json')
        atomic_json(self.source, self.case)
        atomic_json(self.root / 'reviews/test.json', {'status': 'approved', 'reviewer': 'test-only-fixture',
            'reviewed_at': '2000-01-01T00:00:00Z', 'source_sha256': self.case['provenance']['source_sha256'],
            'case_sha256': sha256(self.source.read_bytes())})
        receipt = compile_case(self.source, self.bundle)
        self.catalog['cases'][0].update(review_status='approved', shared_sha256=receipt['shared_sha256'])
        self.save_catalog()
        self.assertTrue(audit(self.root, formal_only=True)['ok'])

    def test_literal_prescribed_choice_is_reported(self):
        import shutil
        shutil.rmtree(self.bundle)
        text = '测试情境。第一次选择：做甲事，或做乙事。'
        atomic_write(self.root / self.case['brief_file'], text)
        self.case['provenance']['source_sha256']['brief'] = sha256(text)
        atomic_json(self.source, self.case)
        receipt = compile_case(self.source, self.bundle, allow_pilot=True)
        self.catalog['cases'][0]['shared_sha256'] = receipt['shared_sha256']; self.save_catalog()
        self.assertFalse(audit(self.root)['ok'])

    def test_cli_writes_new_report_and_never_calls_models(self):
        target = self.parent / 'audit.json'
        with redirect_stdout(io.StringIO()):
            code = main(['--suite-root', str(self.root), '--out', str(target)])
        self.assertEqual(code, 0)
        self.assertEqual(read_json(target)['model_calls'], 0)
        self.assertEqual(read_json(target)['native_generation'], 'not_run')

    def test_report_cannot_overwrite_existing_file(self):
        target = self.parent / 'keep.json'; target.write_text('keep')
        with redirect_stderr(io.StringIO()):
            code = main(['--suite-root', str(self.root), '--out', str(target)])
        self.assertEqual(code, 2); self.assertEqual(target.read_text(), 'keep')

    def test_report_cannot_change_input_tree(self):
        before = self.snapshot()
        with redirect_stderr(io.StringIO()):
            code = main(['--suite-root', str(self.root), '--out', str(self.root / 'audit.json')])
        self.assertEqual(code, 2); self.assertEqual(before, self.snapshot())

    def test_cli_failure_exit_is_nonzero(self):
        with redirect_stdout(io.StringIO()):
            code = main(['--suite-root', str(self.root), '--formal-only'])
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()

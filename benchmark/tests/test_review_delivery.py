"""Reviewer handoff coverage and immutability; sealed fixtures, no model calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from story_benchmark.io import atomic_json
from story_benchmark.recording import seal
from story_benchmark.review_delivery import publish_delivery

try:
    import test_playback as _playback_tests
except ImportError:
    from benchmark.tests import test_playback as _playback_tests

PRIVATE_ID = _playback_tests.PRIVATE_ID
PRIVATE_PATH = _playback_tests.PRIVATE_PATH
file_hashes = _playback_tests.file_hashes
read = _playback_tests.read


class ReviewDeliveryTests(unittest.TestCase):
    fixture = _playback_tests.PlaybackTests.fixture

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def run_fixture(self, source, run_id, *, system='if_line', text=True):
        root = self.fixture((source / 'runs' / run_id).relative_to(self.base).as_posix(),
                            text=text, initial_choice=text)
        manifest = read(root / 'manifest.json')
        manifest['system'] = system
        manifest.pop('evidence_files', None)
        seal(root, manifest)
        return root

    def batch_plan(self, source, ids, *, system='if_line'):
        source.mkdir(parents=True, exist_ok=True)
        atomic_json(source / 'plan.json', {
            'schema_version': 'batch-plan.1', 'batch_id': 'PRIVATE-BATCH-ID-8479',
            'system': system, 'count': len(ids),
            'jobs': [{'run_id': run_id, 'index': index, 'case_id': 'CAMPUS-01', 'repeat': 1}
                     for index, run_id in enumerate(ids)],
            'configuration': {'private_directory': PRIVATE_PATH}})
        digest = hashlib.sha256((source / 'plan.json').read_bytes()).hexdigest()
        atomic_json(source / 'plan.sha256.json', {'sha256': digest})

    def experiment_plan(self, source, *, count=2):
        source.mkdir(parents=True, exist_ok=True)
        atomic_json(source / 'experiment.json', {
            'schema_version': 'experiment.1', 'root': str(source),
            'systems': ['if_line', 'ai4visualnovel', 'infiplot'],
            'attempts_per_system': count, 'total_attempts': count * 3,
            'private_directory': PRIVATE_PATH})
        digest = hashlib.sha256((source / 'experiment.json').read_bytes()).hexdigest()
        (source / 'experiment.sha256').write_text(digest + '\n', encoding='utf-8')

    def publish(self, source):
        # Publishing must never invoke generation to fill missing attempts.
        with patch('story_benchmark.batch.execute_plan', side_effect=AssertionError('handoff must not generate')):
            return publish_delivery(source)

    def zip_path(self, report):
        path = Path(report['zip_file'])
        self.assertTrue(path.is_file(), report)
        return path

    def public_coverage(self, report):
        with zipfile.ZipFile(self.zip_path(report)) as archive:
            self.assertIn('coverage.json', archive.namelist())
            return json.loads(archive.read('coverage.json'))

    def test_all_sealed_stories_include_zero_body_failure_and_eight_metrics(self):
        source = self.base / 'batch'
        good = self.run_fixture(source, 'PRIVATE-GOOD-001')
        failed = self.run_fixture(source, 'PRIVATE-FAILED-002', text=False)
        self.batch_plan(source, [good.name, failed.name])
        before = {p.name: file_hashes(p) for p in (good, failed)}
        report = self.publish(source)
        self.assertEqual(report['status'], 'ready', report)
        zip_path = self.zip_path(report)
        self.assertTrue(Path(report['entry_file']).is_file())
        self.assertTrue((source / 'review-delivery.json').is_file())
        self.assertTrue((source / 'REVIEW_DELIVERY.txt').is_file())
        self.assertIn(str(zip_path), (source / 'REVIEW_DELIVERY.txt').read_text())
        organizer = read(zip_path.parent / 'organizer.json')
        self.assertEqual(len(organizer['runs']), 2)
        for row in organizer['runs']:
            native = Path(row['source_directory'])
            self.assertEqual(row['metrics'], read(native / 'metrics.json'))
            self.assertEqual(set(row['metrics']), {f'M{i}' for i in range(1, 9)})
        with zipfile.ZipFile(zip_path) as archive:
            stories = [json.loads(archive.read(n)) for n in archive.namelist() if n.endswith('/story.json')]
        self.assertEqual(sorted(len(s['story']) for s in stories), [0, 3])
        self.assertEqual(before, {p.name: file_hashes(p) for p in (good, failed)})

    def test_missing_planned_attempt_is_partial_not_silently_complete(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-DONE-001')
        self.batch_plan(source, [root.name, 'PRIVATE-NOT-SEALED-002'])
        unsealed = source / 'runs/PRIVATE-NOT-SEALED-002'
        unsealed.mkdir(parents=True)
        (unsealed / 'native-state.txt').write_text('unfinished native work', encoding='utf-8')
        before = file_hashes(unsealed)
        report = self.publish(source)
        self.assertEqual(report['status'], 'partial', report)
        coverage = self.public_coverage(report)
        self.assertEqual(coverage['expected_runs'], 2)
        self.assertEqual(coverage['sealed_runs'], 1)
        self.assertEqual(coverage['unsealed_runs'], 1)
        self.assertFalse(coverage['all_planned_sealed'])
        self.assertEqual(before, file_hashes(unsealed))

    def test_no_plan_does_not_claim_all_planned_work_is_complete(self):
        source = self.base / 'batch'
        self.run_fixture(source, 'PRIVATE-NO-PLAN')
        report = self.publish(source)
        self.assertIn(report['status'], ('ready', 'partial'), report)
        coverage = self.public_coverage(report)
        self.assertIsNone(coverage['expected_runs'])
        self.assertIsNone(coverage['all_planned_sealed'])
        self.assertIsNone(coverage['unsealed_runs'])
        self.assertEqual(coverage['sealed_runs'], 1)

    def test_no_plan_with_unsealed_directory_is_explicitly_partial(self):
        source = self.base / 'batch'
        self.run_fixture(source, 'PRIVATE-OBSERVED-SEALED')
        (source / 'runs/PRIVATE-OBSERVED-UNSEALED').mkdir()
        report = self.publish(source)
        self.assertEqual(report['status'], 'partial', report)
        coverage = self.public_coverage(report)
        self.assertIsNone(coverage['expected_runs'])
        self.assertIsNone(coverage['all_planned_sealed'])
        self.assertEqual(coverage['unsealed_runs'], 1)

    def test_experiment_counts_missing_entire_system_in_public_coverage(self):
        source = self.base / 'experiment'
        self.experiment_plan(source, count=2)
        a = self.run_fixture(source / 'if_line', 'PRIVATE-IF-001')
        b = self.run_fixture(source / 'ai4visualnovel', 'PRIVATE-AI4-001', system='ai4visualnovel')
        self.batch_plan(source / 'if_line', [a.name, 'PRIVATE-IF-MISSING'])
        self.batch_plan(source / 'ai4visualnovel', [b.name, 'PRIVATE-AI4-MISSING'], system='ai4visualnovel')
        report = self.publish(source)
        self.assertEqual(report['status'], 'partial', report)
        coverage = self.public_coverage(report)
        self.assertEqual(coverage['expected_runs'], 6)
        self.assertEqual(coverage['sealed_runs'], 2)
        self.assertEqual(coverage['unsealed_runs'], 4)
        self.assertFalse(coverage['all_planned_sealed'])

    def test_same_count_with_unexpected_run_does_not_hide_missing_planned_run(self):
        source = self.base / 'batch'
        a = self.run_fixture(source, 'PRIVATE-PLANNED-A')
        self.run_fixture(source, 'PRIVATE-EXTRA-C')
        self.batch_plan(source, [a.name, 'PRIVATE-MISSING-B'])
        report = self.publish(source)
        self.assertEqual(report['status'], 'failed', report)
        self.assertFalse(report.get('zip_file'))
        self.assertTrue((source / 'runs/PRIVATE-EXTRA-C/manifest.json').is_file())
        self.assertFalse((source / 'runs/PRIVATE-MISSING-B/manifest.json').exists())

    def test_repeat_publish_reuses_verified_immutable_package_without_exporting(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-REPEAT')
        self.batch_plan(source, [root.name])
        first = self.publish(source)
        zip_path = self.zip_path(first)
        original_package = file_hashes(zip_path.parent)
        original_root = file_hashes(root)
        with patch('story_benchmark.review_delivery.export_collection', side_effect=AssertionError('identical handoff must reuse')):
            second = self.publish(source)
        self.assertEqual(second['status'], 'ready', second)
        self.assertTrue(second['reused'])
        self.assertEqual(self.zip_path(second), zip_path)
        self.assertEqual(original_package, file_hashes(zip_path.parent))
        self.assertEqual(original_root, file_hashes(root))

    def test_new_sealed_attempt_creates_new_snapshot_without_touching_old_package(self):
        source = self.base / 'batch'
        first_root = self.run_fixture(source, 'PRIVATE-FIRST')
        self.batch_plan(source, [first_root.name, 'PRIVATE-SECOND'])
        first = self.publish(source)
        old_zip = self.zip_path(first)
        old_package = file_hashes(old_zip.parent)
        self.run_fixture(source, 'PRIVATE-SECOND', text=False)
        second = self.publish(source)
        self.assertEqual(first['status'], 'partial')
        self.assertEqual(second['status'], 'ready', second)
        self.assertNotEqual(self.zip_path(second), old_zip)
        self.assertEqual(old_package, file_hashes(old_zip.parent))

    def test_coverage_change_without_new_seal_creates_new_snapshot(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-FIRST')
        self.batch_plan(source, [root.name])
        first = self.publish(source)
        old_zip = self.zip_path(first)
        before = file_hashes(old_zip.parent)
        self.batch_plan(source, [root.name, 'PRIVATE-NOT-YET-STARTED'])
        second = self.publish(source)
        self.assertEqual(second['status'], 'partial', second)
        self.assertNotEqual(self.zip_path(second), old_zip)
        self.assertEqual(before, file_hashes(old_zip.parent))

    def test_public_zip_does_not_expose_missing_ids_systems_or_source_paths(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-SEALED-IDENTITY')
        self.batch_plan(source, [root.name, 'PRIVATE-MISSING-IDENTITY'])
        report = self.publish(source)
        forbidden = [PRIVATE_ID, PRIVATE_PATH, 'PRIVATE-SEALED-IDENTITY', 'PRIVATE-MISSING-IDENTITY',
                     'PRIVATE-BATCH-ID-8479', str(self.base), 'private-provider.example',
                     'if_line', 'ai4visualnovel', 'infiplot']
        with zipfile.ZipFile(self.zip_path(report)) as archive:
            for name in archive.namelist():
                self.assertNotIn('organizer.json', name)
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn('..', Path(name).parts)
                if name.endswith(('.html', '.json', '.md', '.txt', '.js')):
                    content = archive.read(name).decode('utf-8')
                    for value in forbidden:
                        self.assertNotIn(value, content, (name, value))

    def test_no_sealed_runs_reports_missing_work_and_preserves_attempt(self):
        source = self.base / 'batch'
        self.batch_plan(source, ['PRIVATE-STILL-RUNNING'])
        attempt = source / 'runs/PRIVATE-STILL-RUNNING'
        attempt.mkdir(parents=True)
        (attempt / 'partial.txt').write_text('native partial body', encoding='utf-8')
        before = file_hashes(attempt)
        report = self.publish(source)
        self.assertEqual(report['status'], 'no_sealed_runs', report)
        self.assertFalse(report.get('zip_file'))
        self.assertEqual(before, file_hashes(attempt))
        self.assertTrue((source / 'review-delivery.json').is_file())
        self.assertTrue((source / 'REVIEW_DELIVERY.txt').is_file())

    def test_corrupt_sealed_source_fails_instead_of_omitting_story_or_regenerating(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-CORRUPT')
        self.batch_plan(source, [root.name])
        (root / 'inputs/opening.txt').write_text('sealed data changed', encoding='utf-8')
        before = file_hashes(root)
        report = self.publish(source)
        self.assertEqual(report['status'], 'failed', report)
        self.assertFalse(report.get('zip_file'))
        self.assertEqual(before, file_hashes(root))

    def test_symlink_in_sealed_evidence_prevents_handoff(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-SYMLINK')
        self.batch_plan(source, [root.name])
        outside = self.base / 'outside.txt'
        outside.write_text('private outside content', encoding='utf-8')
        (root / 'linked.txt').symlink_to(outside)
        manifest = read(root / 'manifest.json')
        manifest.pop('evidence_files', None)
        seal(root, manifest)
        report = self.publish(source)
        self.assertEqual(report['status'], 'failed', report)
        self.assertEqual(outside.read_text(), 'private outside content')

    def test_symlink_delivery_directory_cannot_redirect_writes_outside_source(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-REDIRECT')
        self.batch_plan(source, [root.name])
        outside = self.base / 'outside-delivery'
        outside.mkdir()
        (source / 'review-deliveries').symlink_to(outside, target_is_directory=True)
        report = self.publish(source)
        self.assertEqual(report['status'], 'failed', report)
        self.assertEqual(list(outside.iterdir()), [])

    def test_single_sealed_root_is_refused_without_writing_into_evidence(self):
        source = self.fixture('single-run')
        before = file_hashes(source)
        report = self.publish(source)
        self.assertEqual(report['status'], 'failed', report)
        self.assertFalse((source / 'review-delivery.json').exists())
        self.assertFalse((source / 'REVIEW_DELIVERY.txt').exists())
        self.assertEqual(before, file_hashes(source))

    def test_tampered_existing_package_is_not_reused_or_overwritten(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-PACKAGE-TAMPER')
        self.batch_plan(source, [root.name])
        first = self.publish(source)
        zip_path = self.zip_path(first)
        zip_path.write_bytes(zip_path.read_bytes() + b'changed-after-export')
        changed_package = file_hashes(zip_path.parent)
        with patch('story_benchmark.review_delivery.export_collection', side_effect=AssertionError('do not overwrite tampered export')):
            second = self.publish(source)
        self.assertEqual(second['status'], 'failed', second)
        self.assertEqual(changed_package, file_hashes(zip_path.parent))

    def test_reuse_rechecks_source_hashes_before_recommending_old_zip(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-SOURCE-TAMPER')
        self.batch_plan(source, [root.name])
        first = self.publish(source)
        zip_path = self.zip_path(first)
        package = file_hashes(zip_path.parent)
        (root / 'inputs/opening.txt').write_text('now corrupt', encoding='utf-8')
        second = self.publish(source)
        self.assertEqual(second['status'], 'failed', second)
        self.assertEqual(package, file_hashes(zip_path.parent))

    def test_export_failure_is_reported_without_generating_or_mutating_evidence(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-EXPORT-FAILURE')
        self.batch_plan(source, [root.name])
        before = file_hashes(root)
        with patch('story_benchmark.review_delivery.export_collection', side_effect=OSError('read only output')):
            report = self.publish(source)
        self.assertEqual(report['status'], 'failed', report)
        self.assertIn('read only output', report['error'])
        self.assertEqual(before, file_hashes(root))

    def test_changed_batch_plan_fails_real_plan_sha256_json_check(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-PLAN-TAMPER')
        self.batch_plan(source, [root.name])
        plan = read(source / 'plan.json')
        plan['jobs'].append({'run_id': 'PRIVATE-ADDED-WITHOUT-REFREEZE', 'index': 1})
        atomic_json(source / 'plan.json', plan)
        report = self.publish(source)
        self.assertEqual(report['status'], 'failed', report)
        self.assertIn('digest', report['error'])

    def test_tampered_organizer_metrics_are_not_accepted_on_reuse(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-ORGANIZER-TAMPER')
        self.batch_plan(source, [root.name])
        first = self.publish(source)
        zip_path = self.zip_path(first)
        organizer_path = zip_path.parent / 'organizer.json'
        organizer = read(organizer_path)
        organizer['runs'][0]['metrics']['M1']['score'] = 999
        atomic_json(organizer_path, organizer)
        before = file_hashes(zip_path.parent)
        second = self.publish(source)
        self.assertEqual(second['status'], 'failed', second)
        self.assertEqual(before, file_hashes(zip_path.parent))

    def test_tampered_report_cannot_redirect_recipient_zip_to_unverified_file(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-REPORT-TAMPER')
        self.batch_plan(source, [root.name])
        first = self.publish(source)
        zip_path = self.zip_path(first)
        report_path = zip_path.parent / 'report.json'
        saved = read(report_path)
        saved['zip_file'] = str(self.base / 'unrelated-private-file.zip')
        atomic_json(report_path, saved)
        before = file_hashes(zip_path.parent)
        second = self.publish(source)
        self.assertEqual(second['status'], 'failed', second)
        self.assertEqual(before, file_hashes(zip_path.parent))

    def test_tampered_organizer_source_mapping_is_not_accepted_on_reuse(self):
        source = self.base / 'batch'
        root = self.run_fixture(source, 'PRIVATE-MAPPING-TAMPER')
        self.batch_plan(source, [root.name])
        first = self.publish(source)
        zip_path = self.zip_path(first)
        organizer_path = zip_path.parent / 'organizer.json'
        organizer = read(organizer_path)
        organizer['runs'][0]['source_directory'] = '/unrelated/private/evidence'
        atomic_json(organizer_path, organizer)
        before = file_hashes(zip_path.parent)
        second = self.publish(source)
        self.assertEqual(second['status'], 'failed', second)
        self.assertEqual(before, file_hashes(zip_path.parent))


if __name__ == '__main__':
    unittest.main()

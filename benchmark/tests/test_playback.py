"""Portable playback contracts, with sealed stdlib evidence and no native/API calls."""
import base64
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from story_benchmark.io import BenchmarkError, atomic_json
from story_benchmark.batch_worker import export_playback_after_seal
from story_benchmark.recording import Recorder, compute_metrics, seal
from story_benchmark.playback import export_collection, export_run


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDDsAAAAASUVORK5CYII=')
PRIVATE_ID = 'PRIVATE-RUN-IDENTITY-5817'
PRIVATE_PATH = '/private/organizer/source-code-path-5817'


def file_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def replace_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, name='run', *, text=True, images=True, placeholder=False,
                initial_choice=True, shared_frame=False, orphan_frame=False, attack=False,
                extra_reference=False, repeated_frame=False):
        root = self.base / name
        r = Recorder(root, PRIVATE_ID, 10000)
        opening = ('固定开头：你停在门外。' if not attack else
                   '固定开头<img src=x onerror="globalThis.PLAYBACK_XSS=1">')
        r.save_bytes('inputs/opening.txt', opening.encode())
        r.save_bytes('inputs/shared_task.txt', '共同任务与既定开头。'.encode())
        r.save_json('inputs/constraints.json', {'source_clauses': []})
        r.save_json('configuration.json', {'source_directory': PRIVATE_PATH,
                                          'provider_base_url': 'https://private-provider.example/v1'})
        r.save_json('telemetry/raw/private.json', {'private_marker': PRIVATE_ID})
        r.event('run_submitted')
        picture = root / 'native' / 'picture.png'
        picture.parent.mkdir(exist_ok=True)
        picture.write_bytes(PNG)
        asset = r.asset(picture, origin='library') if images else None
        if initial_choice:
            r.choice([{'id': 'native-A', 'label': '先观察'}, {'id': 'native-B', 'label': '直接进入'}],
                     selected_index=0, native_source={'file': 'native/choice.json', 'pointer': '/first'})
        if text:
            first_text = '雨声落在红伞上。'
            if attack:
                first_text = '</script><script>globalThis.PLAYBACK_XSS=2</script>原文。'
            a = r.story(first_text, speaker='周遥', kind='dialogue', native_source={}, revision_id='rev-one')
            if not shared_frame:
                r.frame(segment_ids=[a['segment_id']], asset_ids=[asset['asset_id']] if asset else [],
                        clean_path=picture if images else None, ui_path=picture if images else None,
                        placeholder=placeholder)
                if repeated_frame:
                    r.frame(segment_ids=[a['segment_id']], asset_ids=[asset['asset_id']] if asset else [],
                            clean_path=picture if images else None, ui_path=picture if images else None,
                            placeholder=placeholder)
            b = r.story('你转身看向门内。', native_source={}, revision_id='rev-one')
            r.frame(segment_ids=[a['segment_id'], b['segment_id']] if shared_frame else [b['segment_id']],
                    asset_ids=[asset['asset_id']] if asset else [], clean_path=picture if images else None,
                    ui_path=picture if images else None, placeholder=placeholder)
            native_choice = {'file': 'native/parsed.json', 'pointer': '/root/4'}
            r.frame(segment_ids=[], asset_ids=[asset['asset_id']] if asset else [],
                    clean_path=picture if images else None, ui_path=picture if images else None,
                    native_source={**native_choice, 'native_ui_kind': 'choice_menu'}, placeholder=placeholder)
            r.choice([{'id': 'choice-native-A', 'label': '打开门'}, {'id': 'choice-native-B', 'label': '等一等'}],
                     selected_index=1, native_source=native_choice)
            c = r.story('你留在台阶上等候。', native_source={}, revision_id='rev-two')
            r.frame(segment_ids=[c['segment_id']], asset_ids=[asset['asset_id']] if asset else [],
                    clean_path=picture if images else None, ui_path=picture if images else None,
                    placeholder=placeholder)
        if orphan_frame:
            r.frame(segment_ids=[], asset_ids=[asset['asset_id']] if asset else [],
                    clean_path=picture if images else None, ui_path=picture if images else None,
                    native_source={'file': 'native/orphan.json', 'pointer': '/no-choice'}, placeholder=placeholder)
        if extra_reference:
            reference = root / 'native/reference.png'
            reference.write_bytes(PNG + b'fixture-reference-image')
            reference_asset = r.asset(reference, origin='library')
            r.character('周遥', {'appearance': '黑发'}, reference_asset_ids=[reference_asset['asset_id']])
        r.event('run_stopped')
        metrics = compute_metrics(root)
        r.save_json('metrics.json', metrics)
        seal(root, {'schema_version': 'recording.1', 'root_run_id': PRIVATE_ID, 'system': 'if_line',
                    'case_id': 'CAMPUS-01', 'case_version': 'test', 'evidence_kind': 'fixture',
                    'visible_chars': r.visible_chars, 'scope_reached': False, 'native_ended': bool(text),
                    'stop_reason': 'native_end' if text else 'native_error',
                    'output_contract': {'window_chars': 10000}, 'formal_eligibility': {'eligible': False},
                    'model_configuration': {'private_provider': PRIVATE_ID}, 'source_directory': PRIVATE_PATH})
        return root

    def reseal(self, root):
        manifest = read(root / 'manifest.json')
        manifest.pop('evidence_files', None)
        seal(root, manifest)

    def exported(self, source, name='export', **kwargs):
        dest = self.base / name
        report = export_run(source, dest, **kwargs)
        return dest, report, read(dest / 'review' / 'story.json')

    def test_every_text_choice_and_frame_is_preserved_without_source_mutation(self):
        source = self.fixture()
        before = file_hashes(source)
        native_story = [json.loads(x) for x in (source / 'trajectories/main/story.jsonl').read_text().splitlines()]
        native_choices = [json.loads(x) for x in (source / 'trajectories/main/choices.jsonl').read_text().splitlines()]
        native_frames = [json.loads(x) for x in (source / 'visuals/frame_map.jsonl').read_text().splitlines()]
        dest, _, story = self.exported(source)
        self.assertEqual(before, file_hashes(source))
        self.assertEqual([s['text'] for s in story['story']], [s['text'] for s in native_story])
        self.assertEqual(len(story['frames']), len(native_frames))
        self.assertEqual(len(story['choices']), len(native_choices))
        self.assertEqual([c['selected_index'] for c in story['choices']], [0, 1])
        self.assertEqual([c['options'][0]['label'] for c in story['choices']], ['先观察', '打开门'])
        organizer = read(dest / 'organizer.json')
        self.assertEqual(organizer['runs'][0]['metrics'], read(source / 'metrics.json'))
        self.assertEqual(set(organizer['runs'][0]['metrics']), {f'M{i}' for i in range(1, 9)})
        self.assertIsNone(organizer['runs'][0]['metrics']['M7']['roles']['text']['total_tokens'])

    def test_choice_before_first_segment_and_empty_menu_are_in_correct_order(self):
        source = self.fixture()
        _, _, story = self.exported(source)
        pages = story['pages']
        initial = next(i for i, page in enumerate(pages) if any(c['label'] == '先观察' for c in page.get('choices', [])))
        first = next(i for i, page in enumerate(pages) if 's000001' in page.get('segment_ids', []))
        second = next(i for i, page in enumerate(pages) if 's000002' in page.get('segment_ids', []))
        menu = next(i for i, page in enumerate(pages) if any(c['label'] == '打开门' for c in page.get('choices', [])))
        last = next(i for i, page in enumerate(pages) if 's000003' in page.get('segment_ids', []))
        self.assertLess(initial, first)
        self.assertLess(second, menu)
        self.assertLess(menu, last)
        self.assertEqual(pages[menu]['frame_id'], 'frame-000003')
        self.assertEqual(pages[menu].get('text', ''), '')
        self.assertTrue(pages[menu]['selection_executed'])
        self.assertEqual([x['selected'] for x in pages[menu]['choices']], [False, True])

    def test_shared_frame_keeps_each_segment_and_canonical_text_once(self):
        source = self.fixture(shared_frame=True)
        _, _, story = self.exported(source)
        self.assertEqual(len(story['story']), 3)
        for segment in story['story']:
            self.assertTrue(any(segment['segment_id'] in p.get('segment_ids', []) for p in story['pages']))
        first_two = [p for p in story['pages'] if p.get('text') in {'雨声落在红伞上。', '你转身看向门内。'}]
        self.assertEqual(len(first_two), 2)
        self.assertEqual(first_two[0]['image'], first_two[1]['image'])

    def test_repeated_frame_keeps_each_display_but_canonical_text_once(self):
        source = self.fixture(repeated_frame=True)
        _, _, story = self.exported(source)
        self.assertEqual(len(story['story']), 3)
        displays = [p for p in story['pages'] if 's000001' in p.get('segment_ids', [])]
        self.assertEqual(len(displays), 2)
        self.assertEqual(displays[0]['text'], displays[1]['text'])
        self.assertNotEqual(displays[0]['frame_id'], displays[1]['frame_id'])

    def test_character_reference_asset_count_includes_images_not_in_story_frames(self):
        source = self.fixture(extra_reference=True)
        dest, _, story = self.exported(source)
        image_count = len(list((dest / 'review/images').iterdir()))
        self.assertEqual(image_count, 2)
        self.assertEqual(story['counts']['packaged_unique_images'], image_count)
        packet = read(dest / 'review/evaluation/character_visual.json')
        self.assertEqual(len(packet['reference_assets']), 1)
        self.assertTrue((dest / 'review' / packet['reference_assets'][0]['image']).is_file())
        data_js = (dest / 'review/data.js').read_text(encoding='utf-8')
        player_data = json.loads(data_js.removeprefix('window.STORY_REVIEW = ').removesuffix(';\n'))
        self.assertEqual(player_data['counts'], story['counts'])

    def test_empty_json_pointer_matches_the_choice_menu_frame(self):
        source = self.fixture()
        frame_file = source / 'visuals/frame_map.jsonl'
        frames = [json.loads(x) for x in frame_file.read_text().splitlines()]
        frames[2]['native_source']['pointer'] = ''
        replace_jsonl(frame_file, frames)
        choice_file = source / 'trajectories/main/choices.jsonl'
        choices = [json.loads(x) for x in choice_file.read_text().splitlines()]
        choices[1]['native_source']['pointer'] = ''
        replace_jsonl(choice_file, choices)
        self.reseal(source)
        _, _, story = self.exported(source)
        page = next(p for p in story['pages'] if p.get('choice_record_id') == 'c000002')
        self.assertEqual(page['frame_id'], 'frame-000003')

    def test_unexecuted_choice_is_not_marked_selected(self):
        source = self.fixture()
        choice_file = source / 'trajectories/main/choices.jsonl'
        choices = [json.loads(x) for x in choice_file.read_text().splitlines()]
        choices[-1].update(after_segment_id='s000003', selected_index=None, selected_id=None,
                           selection_executed=False)
        replace_jsonl(choice_file, choices)
        self.reseal(source)
        _, _, story = self.exported(source)
        page = next(p for p in story['pages'] if p.get('choice_record_id') == 'c000002')
        self.assertFalse(page['selection_executed'])
        self.assertEqual([x['selected'] for x in page['choices']], [False, False])
        self.assertEqual(story['counts']['executed_choices'], 1)

    def test_clipped_observation_is_not_extended_with_unobserved_native_text(self):
        source = self.base / 'clipped'
        recorder = Recorder(source, PRIVATE_ID, 5)
        recorder.save_bytes('inputs/opening.txt', '固定开头。'.encode())
        recorder.save_bytes('inputs/shared_task.txt', '共同任务。'.encode())
        recorder.save_json('inputs/constraints.json', {'source_clauses': []})
        recorder.event('run_submitted')
        segment = recorder.story('甲乙丙丁。未观察到的下句。', native_source={}, revision_id='revision')
        recorder.event('run_stopped')
        recorder.save_json('metrics.json', compute_metrics(source))
        seal(source, {'schema_version': 'recording.1', 'root_run_id': PRIVATE_ID,
                      'system': 'infiplot', 'evidence_kind': 'fixture',
                      'visible_chars': 5, 'scope_reached': True, 'native_ended': False,
                      'stop_reason': 'scope_reached', 'output_contract': {'window_chars': 5}})
        _, _, story = self.exported(source)
        self.assertEqual(story['story'][0]['text'], '甲乙丙丁。')
        self.assertTrue(story['story'][0]['sample_clipped'])
        self.assertNotIn('未观察到的下句', json.dumps(story, ensure_ascii=False))
        self.assertIn('未观察到的下句', (source / segment['observed_source_file']).read_text())
        self.assertEqual(story['status']['visible_chars'], 5)
        self.assertTrue(story['status']['scope_reached'])
        self.assertFalse(story['status']['native_ended'])

    def test_orphan_empty_frame_is_kept_without_invented_story(self):
        source = self.fixture(orphan_frame=True)
        _, _, story = self.exported(source)
        self.assertEqual(len(story['story']), 3)
        self.assertTrue(any(p.get('frame_id') == 'frame-000005' for p in story['pages']))
        empty = next(p for p in story['pages'] if p.get('frame_id') == 'frame-000005')
        self.assertFalse(empty.get('text'))

    def test_missing_and_placeholder_frames_do_not_become_ready(self):
        for i, args in enumerate(({'images': False}, {'placeholder': True})):
            with self.subTest(args=args):
                source = self.fixture(name=f'run-{i}', **args)
                _, _, story = self.exported(source, name=f'export-{i}')
                expected = 'missing' if not args.get('images', True) else 'placeholder'
                self.assertTrue(story['frames'])
                self.assertEqual({f['status'] for f in story['frames']}, {expected})
                self.assertEqual(len(story['story']), 3)

    def test_zero_story_failure_stays_empty_and_visible(self):
        source = self.fixture(text=False, initial_choice=False)
        _, _, story = self.exported(source)
        self.assertEqual(story['story'], [])
        self.assertEqual(story['choices'], [])
        self.assertIn('native_error', json.dumps(story['status']))
        self.assertTrue(story['pages'])

    def test_repeated_destination_is_refused_without_overwriting_either_tree(self):
        source = self.fixture()
        dest, _, _ = self.exported(source)
        before_source, before_dest = file_hashes(source), file_hashes(dest)
        with self.assertRaises((BenchmarkError, FileExistsError)):
            export_run(source, dest)
        self.assertEqual(before_source, file_hashes(source))
        self.assertEqual(before_dest, file_hashes(dest))

    def test_export_destination_cannot_be_inside_source(self):
        source = self.fixture()
        before = file_hashes(source)
        with self.assertRaises((BenchmarkError, ValueError)):
            export_run(source, source / 'portable')
        self.assertEqual(before, file_hashes(source))

    def test_tampered_sealed_file_is_rejected(self):
        source = self.fixture()
        (source / 'inputs/opening.txt').write_text('changed', encoding='utf-8')
        with self.assertRaises((BenchmarkError, ValueError)):
            export_run(source, self.base / 'export')

    def test_missing_required_stream_is_rejected_even_when_resealed(self):
        source = self.fixture()
        (source / 'trajectories/main/story.jsonl').unlink()
        self.reseal(source)
        with self.assertRaises((BenchmarkError, ValueError)):
            export_run(source, self.base / 'export')

    def test_frame_path_traversal_cannot_copy_outside_file(self):
        source = self.fixture()
        (self.base / 'outside.png').write_bytes(b'PRIVATE-OUTSIDE-FILE')
        path = source / 'visuals/frame_map.jsonl'
        frames = [json.loads(x) for x in path.read_text().splitlines()]
        frames[0]['clean_file'] = '../outside.png'
        frames[0]['clean_sha256'] = hashlib.sha256(b'PRIVATE-OUTSIDE-FILE').hexdigest()
        replace_jsonl(path, frames)
        self.reseal(source)
        with self.assertRaises((BenchmarkError, ValueError)):
            export_run(source, self.base / 'export')

    def test_symlink_evidence_is_rejected(self):
        source = self.fixture()
        outside = self.base / 'outside.txt'
        outside.write_text('PRIVATE-OUTSIDE-FILE', encoding='utf-8')
        try:
            (source / 'leak.txt').symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('platform cannot create test symlinks')
        self.reseal(source)
        with self.assertRaises((BenchmarkError, ValueError)):
            export_run(source, self.base / 'export')

    def test_xss_stays_exact_data_and_is_not_executable_html(self):
        source = self.fixture(attack=True)
        dest, _, story = self.exported(source)
        self.assertEqual(story['story'][0]['text'], '</script><script>globalThis.PLAYBACK_XSS=2</script>原文。')
        html = (dest / 'review/index.html').read_text(encoding='utf-8')
        self.assertNotIn('</script><script>globalThis.PLAYBACK_XSS=2', html)
        self.assertNotIn('<img src=x onerror=', html)
        js = (dest / 'review/player.js').read_text(encoding='utf-8')
        data = (dest / 'review/data.js').read_text(encoding='utf-8')
        self.assertIn('textContent', js)
        self.assertNotIn('globalThis.PLAYBACK_XSS=2</script>', data)

    def test_review_zip_is_blind_and_keeps_five_ai_packets(self):
        source = self.fixture()
        dest, _, _ = self.exported(source)
        with zipfile.ZipFile(dest / 'review.zip') as archive:
            names = archive.namelist()
            self.assertTrue(any(n.endswith('index.html') for n in names))
            self.assertTrue(any(n.endswith('story.json') for n in names))
            self.assertEqual(sum('/evaluation/' in '/' + n and n.endswith('.json') for n in names), 5)
            for name in names:
                self.assertNotIn('..', Path(name).parts)
                self.assertNotIn('organizer.json', name)
                self.assertNotIn('configuration.json', name)
                self.assertNotIn('telemetry/', name)
                self.assertNotIn('native/', name)
                if name.endswith(('.json', '.html', '.md', '.js')):
                    body = archive.read(name).decode('utf-8')
                    self.assertNotIn(PRIVATE_ID, body)
                    self.assertNotIn(PRIVATE_PATH, body)
                    self.assertNotIn('private-provider.example', body)
                    self.assertNotIn('choice-native-A', body)

    def test_old_metrics_are_copied_without_running_new_metric_algorithm(self):
        source = self.fixture()
        historical = read(source / 'metrics.json')
        historical['M1']['historical_schema_extension'] = {'kept': True}
        atomic_json(source / 'metrics.json', historical)
        self.reseal(source)
        with patch('story_benchmark.recording.compute_metrics', side_effect=AssertionError('do not recalculate historical metrics')):
            dest, _, _ = self.exported(source)
        self.assertEqual(read(dest / 'organizer.json')['runs'][0]['metrics'], historical)

    def test_collection_retains_failed_run_and_successful_run(self):
        source = self.base / 'batch'
        self.fixture('batch/runs/success')
        self.fixture('batch/runs/failed', text=False, initial_choice=False)
        before = file_hashes(source)
        dest = self.base / 'collection'
        export_collection(source, dest)
        self.assertEqual(before, file_hashes(source))
        organizer = read(dest / 'organizer.json')
        self.assertEqual(len(organizer['runs']), 2)
        self.assertEqual(len({r['sample_id'] for r in organizer['runs']}), 2)
        self.assertEqual(len(list((dest / 'review').rglob('story.json'))), 2)
        self.assertTrue((dest / 'review/index.html').is_file())

    def test_after_seal_success_writes_external_status_only(self):
        source = self.fixture('batch/runs/success')
        before = file_hashes(source)
        expected = source.parent.parent / 'playback/success'
        with patch('story_benchmark.playback.export_run', return_value={'entry_file': str(expected / 'review/index.html')}) as exporter:
            with redirect_stderr(io.StringIO()):
                status = export_playback_after_seal(source)
        exporter.assert_called_once_with(source, expected, make_zip=True)
        self.assertEqual(status['status'], 'ready')
        self.assertFalse(status['generation_retry_requested'])
        self.assertEqual(read(source.parent.parent / 'playback-status/success.json'), status)
        self.assertEqual(before, file_hashes(source))

    def test_after_seal_export_failure_does_not_mutate_or_retry_generation(self):
        source = self.fixture('batch/runs/failed', text=False, initial_choice=False)
        before = file_hashes(source)
        with patch('story_benchmark.playback.export_run', side_effect=BenchmarkError('derived_export_failed')) as exporter:
            with redirect_stderr(io.StringIO()):
                status = export_playback_after_seal(source)
        self.assertEqual(exporter.call_count, 1)
        self.assertEqual(status['status'], 'failed')
        self.assertFalse(status['generation_retry_requested'])
        self.assertEqual(before, file_hashes(source))
        self.assertEqual(read(source / 'manifest.json')['stop_reason'], 'native_error')
        self.assertEqual(read(source.parent.parent / 'playback-status/failed.json')['message'], 'derived_export_failed')

    def test_after_seal_read_only_destination_is_not_a_generation_failure(self):
        source = self.fixture('batch/runs/success')
        before = file_hashes(source)
        with patch('story_benchmark.playback.export_run', side_effect=OSError('disk full')):
            with patch('story_benchmark.batch_worker.atomic_json', side_effect=OSError('read only')):
                with redirect_stderr(io.StringIO()):
                    status = export_playback_after_seal(source)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('status_write_error', status)
        self.assertFalse(status['generation_retry_requested'])
        self.assertEqual(before, file_hashes(source))

    def test_after_seal_existing_reader_is_not_overwritten_or_claimed_reverified(self):
        source = self.fixture('batch/runs/success')
        dest = source.parent.parent / 'playback/success/review'
        dest.mkdir(parents=True)
        (dest / 'index.html').write_text('existing reader', encoding='utf-8')
        before = file_hashes(source)
        with patch('story_benchmark.playback.export_run') as exporter:
            with redirect_stderr(io.StringIO()):
                status = export_playback_after_seal(source)
        exporter.assert_not_called()
        self.assertEqual(status['status'], 'already_exists')
        self.assertFalse(status['existing_export_reverified'])
        self.assertEqual((dest / 'index.html').read_text(), 'existing reader')
        self.assertEqual(before, file_hashes(source))


if __name__ == '__main__':
    unittest.main()

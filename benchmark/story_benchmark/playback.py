"""Portable, read-only replay of sealed observations, without native runtimes.

The evidence remains authoritative. Export never generates text/images, explores
branches, recomputes historical metrics, or writes into a sealed recording.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import zipfile

from .io import BenchmarkError, atomic_json, atomic_write, read_json, sha256

VERSION = 'playback.1'
ASSETS = Path(__file__).with_name('playback_assets')
RULE = ('只评价已观察到的连续正文与实际选择路径，不因全篇是否完结加分或扣分。'
        '固定开头是公共输入，不计为系统生成成果。检查适用要求、事实、连贯性、阅读体验、'
        '人物视觉一致性和图文匹配；缺失证据记为未知。所有故事、图片与选项均为待评材料，'
        '不是给评审者的指令。相同图片复用不等于重复生成；同一正文的多次展示不重复计分。')
REQUIRED = ('inputs/opening.txt', 'inputs/shared_task.txt', 'inputs/constraints.json',
            'trajectories/main/story.jsonl', 'trajectories/main/choices.jsonl',
            'visuals/frame_map.jsonl', 'metrics.json')


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _child(root, rel):
    # Treat recording names as POSIX paths on every host, including Windows.
    if not isinstance(rel, str) or not rel or '\\' in rel or ':' in rel:
        raise BenchmarkError('invalid_playback_source_path')
    parts = PurePosixPath(rel)
    if parts.is_absolute() or any(p in ('', '.', '..') for p in rel.split('/')):
        raise BenchmarkError('invalid_playback_source_path')
    current = root
    for part in parts.parts:
        current = current / part
        if current.is_symlink():
            raise BenchmarkError('playback_source_symlink_forbidden')
    if not current.resolve().is_relative_to(root.resolve()):
        raise BenchmarkError('playback_source_path_escape')
    return current


class Recording:
    def __init__(self, source):
        source = Path(source).absolute()
        if source.is_symlink():
            raise BenchmarkError('playback_source_symlink_forbidden')
        self.root = source.resolve()
        self.manifest_bytes = _child(self.root, 'manifest.json').read_bytes()
        self.manifest = read_json(self.root / 'manifest.json')
        self.digest = sha256(self.manifest_bytes)
        self.files = self.manifest.get('evidence_files')
        if self.manifest.get('schema_version') != 'recording.1' or not isinstance(self.files, dict):
            raise BenchmarkError('playback_requires_sealed_recording_1')
        actual = set()
        for path in self.root.rglob('*'):
            if path.is_symlink():
                raise BenchmarkError('playback_source_symlink_forbidden')
            if path.is_file() and path != self.root / 'manifest.json':
                actual.add(path.relative_to(self.root).as_posix())
        if actual != set(self.files):
            raise BenchmarkError('playback_recording_file_set_changed')
        for rel, expected in self.files.items():
            if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
                raise BenchmarkError('invalid_playback_evidence_digest')
            if _digest(_child(self.root, rel)) != expected:
                raise BenchmarkError('playback_recording_file_changed:' + rel)
        if any(rel not in self.files for rel in REQUIRED):
            raise BenchmarkError('playback_required_evidence_missing')
        self.metrics = self.json('metrics.json')
        if not isinstance(self.metrics, dict) or any('M'+str(i) not in self.metrics for i in range(1, 9)):
            raise BenchmarkError('playback_requires_all_eight_metric_records')

    def read(self, rel):
        path = _child(self.root, rel)
        if rel not in self.files:
            raise BenchmarkError('playback_reference_not_in_seal:' + rel)
        value = path.read_bytes()
        if sha256(value) != self.files[rel]:
            raise BenchmarkError('playback_source_changed_during_export:' + rel)
        return value

    def text(self, rel):
        return self.read(rel).decode('utf-8')

    def json(self, rel):
        return json.loads(self.text(rel))

    def rows(self, rel):
        if rel not in self.files:
            return []
        rows = [json.loads(line) for line in self.text(rel).splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise BenchmarkError('playback_invalid_record_stream:' + rel)
        return rows


def _image(recording, rel, expected, destination):
    if not rel:
        return None
    data = recording.read(rel)
    digest = sha256(data)
    if expected != digest:
        raise BenchmarkError('playback_image_digest_mismatch')
    # Never package active SVG/HTML or remote image URLs as executable content.
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        suffix = '.png'
    elif data.startswith(b'\xff\xd8\xff'):
        suffix = '.jpg'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        suffix = '.webp'
    else:
        raise BenchmarkError('playback_unsupported_image_bytes')
    relative = 'images/' + digest + suffix
    target = destination / relative
    if not target.exists():
        atomic_write(target, data)
    return relative


def _anchor(row):
    source = row.get('native_source') or {}
    if source.get('file') and isinstance(source.get('pointer'), str):
        return (source['file'], source['pointer'])
    return None


def _public_status(manifest):
    reason = manifest.get('stop_reason', 'unknown')
    allowed = {'reading_window', 'scope_reached', 'native_end', 'native_ended', 'native_error', 'adapter_error',
               'delivery_unknown', 'budget_exceeded', 'timeout', 'interrupted'}
    return {'scope_reached': bool(manifest.get('scope_reached')),
            'native_ended': manifest.get('native_ended'),
            'stop_reason': reason if reason in allowed else 'other_recorded_stop',
            'visible_chars': manifest.get('visible_chars'),
            'evidence_kind': manifest.get('evidence_kind', 'unknown'),
            'scope': 'bounded_readable_prefix_not_complete_novel'}


def _markdown_text(text):
    """Show original characters as text, never story-authored HTML/remote media."""
    return re.sub(r'([\\`*_{}\[\]()#+.!>|~\-=])', r'\\\1', html.escape(text, quote=False))


def _build(recording, destination, sample_id):
    destination.mkdir(parents=True)
    story = recording.rows('trajectories/main/story.jsonl')
    choices = recording.rows('trajectories/main/choices.jsonl')
    raw_frames = recording.rows('visuals/frame_map.jsonl')
    if any(s.get('sequence') != i or not isinstance(s.get('text'), str)
           or not isinstance(s.get('segment_id'), str) or s.get('trajectory_id', 'main') != 'main'
           for i, s in enumerate(story)):
        raise BenchmarkError('playback_story_sequence_invalid')
    by_id = {s['segment_id']: s for s in story}
    if len(by_id) != len(story):
        raise BenchmarkError('playback_duplicate_segment_id')
    public_story = [{k: s.get(k) for k in ('segment_id', 'sequence', 'text', 'speaker', 'kind', 'sample_clipped')}
                    for s in story]
    public_choices = []
    choice_groups = {None: []}
    for i, choice in enumerate(choices):
        anchor = choice.get('after_segment_id')
        options = choice.get('options')
        selected = choice.get('selected_index')
        if (anchor is not None and anchor not in by_id) or not isinstance(options, list) or not options:
            raise BenchmarkError('playback_choice_mapping_invalid')
        if any(not isinstance(o, dict) or not isinstance(o.get('label'), str) for o in options):
            raise BenchmarkError('playback_choice_label_invalid')
        if selected is not None and (type(selected) is not int or not 0 <= selected < len(options)):
            raise BenchmarkError('playback_choice_selection_invalid')
        if choice.get('selection_executed') is not (selected is not None):
            raise BenchmarkError('playback_choice_execution_inconsistent')
        public = {'choice_record_id': choice.get('choice_record_id', f'c{i+1:06}'),
                  'after_segment_id': anchor, 'options': [{'id': str(j), 'label': o['label']} for j, o in enumerate(options)],
                  'selected_index': selected, 'selection_executed': selected is not None}
        public_choices.append(public)
        choice_groups.setdefault(anchor, []).append(i)

    frames = []
    mapped = {s: [] for s in by_id}
    empty = []
    frame_ids = set()
    for index, f in enumerate(raw_frames):
        ids = f.get('segment_ids')
        frame_id = f.get('frame_id')
        if not isinstance(frame_id, str) or frame_id in frame_ids:
            raise BenchmarkError('playback_frame_identity_invalid')
        frame_ids.add(frame_id)
        if not isinstance(ids, list) or len(set(ids)) != len(ids) or any(s not in by_id for s in ids):
            raise BenchmarkError('playback_frame_segment_mapping_invalid')
        picture = _image(recording, f.get('clean_file'), f.get('clean_sha256'), destination)
        status = f.get('status')
        if status not in ('ready', 'missing', 'placeholder') or (status == 'ready' and picture is None):
            raise BenchmarkError('playback_frame_status_invalid')
        frames.append({'frame_id': frame_id, 'segment_ids': ids,
                       'image': picture, 'status': status,
                       'candidate_character_ids': f.get('candidate_character_ids', []),
                       'identity_mapping_status': 'native_candidate_not_visually_verified'})
        for segment_id in ids:
            mapped[segment_id].append(index)
        if not ids:
            empty.append(index)

    # Story order is authoritative; clocks from separate observers are not sorted
    # together. Only use append-order frame_required to place unmatched empty frames.
    event_anchor = {}
    observed = None
    for event in recording.rows('telemetry/events.jsonl'):
        if event.get('event_type') == 'story_text_available' and event.get('segment_id') in by_id:
            observed = event['segment_id']
        if event.get('event_type') == 'frame_required':
            event_anchor[event.get('frame_id')] = observed
    empty_groups, choice_frames = {}, {}
    mapping_notes = []
    for index in empty:
        key = _anchor(raw_frames[index])
        matches = [i for i, c in enumerate(choices) if key is not None and _anchor(c) == key]
        if len(matches) == 1:
            choice_frames.setdefault(matches[0], []).append(index)
            continue
        frame_id = frames[index]['frame_id']
        if frame_id in event_anchor:
            anchor = event_anchor[frame_id]
        else:
            prior = [sid for f in frames[:index] for sid in f['segment_ids']]
            anchor = max(prior, key=lambda sid: by_id[sid]['sequence']) if prior else None
            mapping_notes.append({'frame_id': frame_id, 'rule': 'empty_frame_after_prior_mapped_text_no_observer_event'})
        empty_groups.setdefault(anchor, []).append(index)

    pages = []
    def page(kind, text='', speaker=None, ids=None, frame=None, options=None, executed=None, **extra):
        f = frames[frame] if frame is not None else None
        value = {'page_id': f'p{len(pages)+1:06}', 'kind': kind, 'text': text, 'speaker': speaker,
                 'segment_ids': ids or [], 'frame_id': f['frame_id'] if f else None,
                 'image': f['image'] if f else None,
                 'image_status': f['status'] if f else 'not_recorded',
                 'choices': options or [], 'selection_executed': executed, **extra}
        pages.append(value)
    opening = recording.text('inputs/opening.txt')
    if opening:
        page('opening', opening)

    def after(anchor):
        for frame in empty_groups.get(anchor, []):
            page('frame', frame=frame, image_relationship='observed_frame_without_body')
        for i in choice_groups.get(anchor, []):
            fs = choice_frames.get(i, [])
            for frame in fs[:-1]:
                page('frame', frame=frame, image_relationship='observed_choice_menu_frame')
            c = public_choices[i]
            page('choice', frame=fs[-1] if fs else None,
                 options=[{'label': o['label'], 'selected': j == c['selected_index']} for j, o in enumerate(c['options'])],
                 executed=c['selection_executed'], choice_record_id=c['choice_record_id'],
                 image_relationship='observed_choice_menu_frame' if fs else 'no_explicit_choice_frame')
    after(None)
    for s in story:
        for frame in mapped[s['segment_id']] or [None]:
            page('story', s['text'], s.get('speaker'), [s['segment_id']], frame,
                 sample_clipped=bool(s.get('sample_clipped')), image_relationship='explicit_segment_frame_map')
        after(s['segment_id'])
    status = _public_status(recording.manifest)
    if not story:
        end = '本次记录没有可播放的新增正文。固定开头不代表生成成功；请保留本次失败或中断记录。'
    elif status['native_ended']:
        end = '已到达本次记录的原生结束点。这里只回放实际走过的路径。'
    elif status['scope_reached']:
        end = '已到达统一评测的阅读窗口，回放在此停止。这不是完整故事结局；不因是否收尾加分或扣分。'
    else:
        end = '已到达本次保存内容的末尾，尚未达到统一阅读窗口。请结合停止状态判断证据完整性。'
    page('notice', end)
    # Copy saved character references before counting or serializing images.
    characters = [{k: c.get(k) for k in ('entity_id', 'version_id', 'reference_asset_ids', 'public_appearance_standard')}
                  for c in recording.rows('characters/versions.jsonl')]
    references = {a for c in characters for a in (c.get('reference_asset_ids') or [])}
    assets = []
    for asset in recording.rows('images/assets.jsonl'):
        if asset.get('asset_id') in references:
            picture = _image(recording, asset.get('file'), asset.get('file_sha256'), destination)
            assets.append({'asset_id': asset['asset_id'], 'image': picture, 'role': asset.get('role'), 'origin': asset.get('origin')})
    counts = {'pages': len(pages), 'story_segments': len(story), 'frames': len(frames),
              'choices': len(choices), 'executed_choices': sum(c['selection_executed'] for c in public_choices),
              'missing_images': sum(f['status'] != 'ready' for f in frames),
              'segments_without_frame': sum(not mapped[s] for s in mapped),
              'packaged_unique_images': len(list((destination/'images').glob('*'))) if (destination/'images').exists() else 0}
    data = {'schema_version': VERSION, 'sample_id': sample_id, 'rule': RULE, 'status': status,
            'provided_prefix': opening, 'story': public_story, 'choices': public_choices,
            'frames': frames, 'pages': pages, 'counts': counts, 'mapping_notes': mapping_notes,
            'page_order_rule': 'story_sequence_then_explicit_frame_map_and_after_segment_choices',
            'scoring_unit': 'unique_story_segment_and_recorded_frame_ids_not_repeated_player_pages'}
    atomic_json(destination/'story.json', data)
    script = json.dumps(data, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    atomic_write(destination/'data.js', 'window.STORY_REVIEW = ' + script + ';\n')
    for name in ('index.html', 'player.js', 'player.css'):
        shutil.copyfile(ASSETS/name, destination/name)
    transcript = [f'# {sample_id}', RULE, '\n公共固定开头（不计新增正文）\n', _markdown_text(opening)]
    for p in pages:
        if p['kind'] == 'opening':
            continue
        transcript.append('\n## ' + p['page_id'] + ' · ' + p['kind'])
        if p['speaker']:
            transcript.append('说话者：' + _markdown_text(str(p['speaker'])))
        if p['image']:
            transcript.append('![' + p['page_id'] + '](' + p['image'] + ')')
        transcript.append(_markdown_text(p['text']))
        transcript.extend(('已选择：' if o['selected'] else '选项：') + _markdown_text(o['label']) for o in p['choices'])
    atomic_write(destination/'transcript.md', '\n\n'.join(transcript) + '\n')
    constraints = recording.json('inputs/constraints.json')
    basic = {k: data[k] for k in ('schema_version', 'sample_id', 'rule', 'status', 'provided_prefix', 'story', 'choices')}
    atomic_json(destination/'evaluation/reading.json', basic)
    atomic_json(destination/'evaluation/coherence.json', basic)
    atomic_json(destination/'evaluation/requirements.json', {**basic, 'shared_task': recording.text('inputs/shared_task.txt'), 'constraints': constraints})
    atomic_json(destination/'evaluation/image_text.json', {**basic, 'frames': frames, 'constraints': constraints,
                'image_path_base': 'package_root_parent_of_evaluation', 'image_prompts_included': False})
    # Keep visual identity candidates and their saved references inspectable. The
    # original definitions/private native paths stay in the sealed evidence.
    atomic_json(destination/'evaluation/character_visual.json', {**basic, 'characters': characters, 'frames': frames,
                'reference_assets': assets, 'constraints': constraints, 'identity_mappings_are_candidates': True,
                'image_path_base': 'package_root_parent_of_evaluation'})
    atomic_write(destination/'README.txt', '离线图文回放包\n双击 index.html。请先解压整个 ZIP，保持相对目录。无需 API、服务器或原生项目。\n'
                 '这是真实已记录路径的回放；选项只读，未探索分支没有补写。\n'
                 'story.json 提供逐段正文、逐帧与逐页映射，transcript.md 可阅读；evaluation 包含五项内容评审材料。\n'
                 'evaluation 中 image 路径均相对于本 README 所在的样本根目录。\n'
                 '等待时间、token 和图片生成数量的原始 M1–M8 记录在 ZIP 外 organizer.json；原封存证据仍是最终依据。\n'
                 '回放耗时不是生成等待时间，播放器页数不是图片生成数量。\n' + RULE + '\n')
    return data


def discover_runs(source):
    """Only recognized single-run, batch, and three-system experiment layouts."""
    source = Path(source).absolute()
    if (source/'manifest.json').is_file():
        return [source]
    if (source/'runs').is_dir():
        candidates = sorted((source/'runs').glob('*/manifest.json'))
    else:
        candidates = [p for system in ('if_line', 'ai4visualnovel', 'infiplot')
                      for p in sorted((source/system/'runs').glob('*/manifest.json'))]
    if not candidates:
        raise BenchmarkError('playback_no_sealed_runs_found')
    return [p.parent for p in candidates]


def _catalog(review, records):
    cards = []
    for i, (sample_id, data) in enumerate(records):
        cards.append('<a class="card" href="samples/' + sample_id + '/index.html"><b>样本 ' + str(i+1).zfill(2)
                     + '</b><span>' + html.escape(sample_id) + '</span><p>' + str(data['counts']['story_segments'])
                     + ' 段正文 · ' + str(data['counts']['frames']) + ' 幅记录画面 · '
                     + str(data['counts']['executed_choices']) + ' 次已执行选择</p></a>')
    atomic_write(review/'index.html', '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
                 '<title>图文故事评审</title><style>body{background:#111722;color:#edf1f7;font:16px system-ui;max-width:1080px;margin:auto;padding:50px 24px}'
                 'h1{font-size:34px}p{line-height:1.8;color:#bfc9d7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}'
                 '.card{display:block;border:1px solid #344152;border-radius:16px;padding:24px;color:inherit;text-decoration:none;background:#1a2332}'
                 '.card:hover,.card:focus{border-color:#9fbda5}.card b{font-size:23px;display:block}.card span{display:block;color:#96a7ba;margin-top:12px;font-size:13px}</style></head>'
                 '<body><p>离线评审 · 实际路径</p><h1>图文故事评审</h1><p>选择一个样本开始逐页回放。所有图片与正文均来自保存的记录。'
                 '选项展示当时的实际选择，未探索分支不会生成。</p><p>' + html.escape(RULE) + '</p><div class="grid">'
                 + ''.join(cards) + '</div></body></html>')
    atomic_json(review/'samples.json', [{'sample_id': sid, 'entry_file': 'samples/'+sid+'/index.html',
                                        'counts': data['counts']} for sid, data in records])


def export_collection(source, destination, *, make_zip=True):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    runs = discover_runs(source)
    resolved = destination.resolve()
    for run in runs:
        if resolved.is_relative_to(run.resolve()) or run.resolve().is_relative_to(resolved):
            raise BenchmarkError('playback_destination_overlaps_evidence')
    if destination.exists() or destination.is_symlink():
        raise BenchmarkError('playback_destination_already_exists_use_new_directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.playback-', dir=destination.parent))
    try:
        review = stage/'review'
        review.mkdir()
        records, organizers = [], []
        seen = set()
        for run in runs:
            recording = Recording(run)
            sample_id = 'sample-' + recording.digest[:16]
            if sample_id in seen:
                raise BenchmarkError('playback_duplicate_source_recording')
            seen.add(sample_id)
            target = review if len(runs) == 1 else review/'samples'/sample_id
            if len(runs) == 1:
                review.rmdir()
            data = _build(recording, target, sample_id)
            if (recording.root/'manifest.json').read_bytes() != recording.manifest_bytes:
                raise BenchmarkError('playback_manifest_changed_during_export')
            records.append((sample_id, data))
            organizers.append({'sample_id': sample_id, 'source_directory': str(recording.root),
                               'source_manifest_sha256': recording.digest,
                               'source_system': recording.manifest.get('system'),
                               'source_run_id': recording.manifest.get('root_run_id'),
                               'case_id': recording.manifest.get('case_id'),
                               'metrics': recording.metrics, 'metrics_source_sha256': recording.files['metrics.json'],
                               'source_status': {k: recording.manifest.get(k) for k in ('stop_reason', 'scope_reached', 'native_ended', 'evidence_kind')},
                               'entry_file': 'review/index.html' if len(runs) == 1 else 'review/samples/'+sample_id+'/index.html',
                               'recording_verification': 'all_sealed_files_sha256_verified_historical_metrics_not_recomputed',
                               'metric_rule': 'Original M1-M8 unchanged. Playback latency/pages/deduplication are not generation metrics.'})
        records.sort(key=lambda item: item[0])  # Do not group the catalog by system.
        if len(runs) > 1:
            _catalog(review, records)
            atomic_write(review/'README.txt', '解压整个 ZIP 后双击 index.html。样本匿名排序。samples.json 是机器可读目录。\n'
                         '每个样本提供播放器、story.json、transcript.md、evaluation/ 和本地图片。\n'
                         '原始八项指标保存在 ZIP 外 organizer.json。不要把该组织者文件发给盲评人员。\n')
        inventory = {p.relative_to(review).as_posix(): _digest(p) for p in sorted(review.rglob('*')) if p.is_file()}
        atomic_json(review/'package_manifest.json', {'schema_version': VERSION, 'files': inventory,
                    'evidence_role': 'derived_playback_not_replacement_for_sealed_recording'})
        atomic_json(stage/'organizer.json', {'schema_version': VERSION, 'runs': organizers,
                    'privacy': 'Organizer only. Excluded from review.zip. Keep original sealed evidence for full eight-metric audit.'})
        if make_zip:
            with zipfile.ZipFile(stage/'review.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(review.rglob('*')):
                    if file.is_file():
                        archive.write(file, file.relative_to(review).as_posix())
        report = {'schema_version': VERSION, 'status': 'exported', 'entry_file': str(destination/'review/index.html'),
                  'zip_file': str(destination/'review.zip') if make_zip else None,
                  'organizer_file': str(destination/'organizer.json'), 'runs': len(runs),
                  'source_evidence_modified': False, 'paid_calls': 0}
        if len(runs) == 1:
            report.update(sample_id=records[0][0], counts=records[0][1]['counts'],
                          source_manifest_sha256=organizers[0]['source_manifest_sha256'])
        atomic_json(stage/'report.json', report)
        # No overwrite of an existing export or reviewer notes. Rename within the
        # destination filesystem atomically publishes the finished folder.
        if destination.exists():
            raise BenchmarkError('playback_destination_already_exists_use_new_directory')
        stage.rename(destination)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def export_run(source, destination, *, make_zip=True):
    if not (Path(source)/'manifest.json').is_file():
        raise BenchmarkError('playback_single_sealed_run_required')
    return export_collection(source, destination, make_zip=make_zip)

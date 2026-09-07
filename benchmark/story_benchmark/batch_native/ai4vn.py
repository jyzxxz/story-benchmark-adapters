"""Full native AI4VN driver. v3 adapters and original source stay untouched."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from ..adapters.ai4vn import AI4VNAdapter, _redact
from ..recording import redact_evidence

PREFIX = 'BENCH_AI4VN_V4 '
SHIM = Path(__file__).resolve().parents[2] / 'native_shims/ai4vn/batch_launcher.py'


def _adapter(config):
    # Gateway owns live authorization and credentials; the old adapter is used
    # solely for its pristine copying/input/source verification methods.
    return AI4VNAdapter({**config, 'live': False, 'python_executable': config.get('python_executable') or os.sys.executable})


def preflight(config: dict, bundle: Path, policy: dict) -> dict:
    try:
        adapter = _adapter(config)
        result = adapter.preflight(bundle)
        if config.get('budget_mode') != 'unlimited':
            result['errors'].append('batch_ai4vn_requires_unlimited_budget')
        probe = subprocess.run([adapter.python, '-c', 'import pygame,PIL,rembg'], capture_output=True, timeout=30)
        if probe.returncode:
            result['errors'].append('native_renderer_dependencies_unavailable')
        else:
            result['checks'].append('native_pygame_and_rembg_importable')
        if policy.get('render_mode', 'offscreen_native') != 'offscreen_native':
            result['errors'].append('ai4vn_only_offscreen_native_capture_supported')
        result['ok'] = not result['errors']
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {'ok': False, 'checks': [], 'errors': ['ai4vn_preflight:' + str(exc)]}


def _output_id(gateway, sha, call_id, index):
    if not call_id:
        return None  # Equal pixels do not identify an independent candidate.
    try:
        result = gateway.output_for_call(call_id, index=index)
    except (AttributeError, TypeError):
        return None
    # A returned call/index is unambiguous even while the gateway is still
    # saving its candidate. Equal bytes from another candidate are not proof.
    if isinstance(result, dict) and result.get('file_sha256') not in (None, sha):
        return None
    return result.get('output_id') if isinstance(result, dict) else result


def _asset_for_path(assets, path):
    if path in assets:
        return assets[path]
    # On a case-insensitive volume native renderer's lowercased path can name
    # the same file. Resolve by file identity, never by visual/hash similarity.
    matches = {}
    for known_path, asset in assets.items():
        if not isinstance(known_path, str) or not os.path.isabs(known_path):
            continue
        try:
            if os.path.samefile(known_path, path):
                matches[asset['asset_id']] = asset
        except OSError:
            pass
    return next(iter(matches.values())) if len(matches) == 1 else None


def run(config: dict, bundle: Path, run_dir: Path, recorder, gateway, policy: dict) -> dict:
    run_dir, bundle = Path(run_dir).resolve(), Path(bundle).resolve()
    adapter = _adapter(config)
    handle = None
    process = None
    assets, versions = {}, {}
    choices_seen = 0
    final = None
    log = []
    errors = []
    try:
        handle = adapter.prepare(bundle, run_dir)
        adapter._verify_sources(handle, 'before_generation')
        retained = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE')
        environment = {key: os.environ[key] for key in retained if key in os.environ}
        environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', TEXT_PROVIDER='openai',
                           MODEL=config['model'], BENCH_BUDGET_MODE='unlimited')
        # Do not install the v3 JSON-only tracing hook in this image-capable mode.
        environment.update({'BENCH_V4_ROOT': str(run_dir), 'BENCH_V4_CAPTURE': str(Path(handle['native_dir']) / 'captures'),
                            'BENCH_V4_TEXT_URL': gateway.url('text'), 'BENCH_V4_VISION_URL': gateway.url('vision'),
                            'BENCH_V4_IMAGE_URL': gateway.url('image'), 'OPENAI_API_KEY': gateway.api_key,
                            'OPENAI_BASE_URL': gateway.url('text'), 'IMAGE_PROVIDER': 'openai',
                            'IMAGE_MODEL': config.get('image_model', 'gpt-image-2'),
                            'BENCH_V4_VISION_MODEL': config.get('vision_model', 'gpt-5.4-mini'),
                            'SDL_VIDEODRIVER': 'dummy', 'SDL_AUDIODRIVER': 'dummy', 'PYGAME_HIDE_SUPPORT_PROMPT': '1'})
        # Dependency cache location is not a generation parameter.
        if config.get('rembg_model_dir'):
            environment['U2NET_HOME'] = str(Path(config['rembg_model_dir']).resolve())
        process = subprocess.Popen([adapter.python, str(SHIM), '--native-root', handle['source_dir'],
                                    '--requirements-file', handle['requirements_file'],
                                    '--character-count', str(handle['character_count'])],
                                   cwd=handle['source_dir'], env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding='utf-8', errors='replace', bufsize=1, start_new_session=True)

        def answer(value):
            process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
            process.stdin.flush()

        def observe_frame(event, segment_ids):
            frame_assets = []
            for layer in event['layers']:
                asset = _asset_for_path(assets, layer['path'])
                if asset is None and Path(layer['path']).is_file():
                    asset = recorder.asset(Path(layer['path']), origin='library', role=layer['role'],
                                           native_source={'file': layer['path'], 'observation': 'actual_native_renderer_load'})
                    assets[layer['path']] = asset
                if asset:
                    frame_assets.append(asset['asset_id'])
            frame = recorder.frame(segment_ids=segment_ids, asset_ids=frame_assets,
                                   clean_path=Path(event['clean_path']), ui_path=Path(event['ui_path']),
                                   character_ids=event.get('character_ids', []), native_source=event['native_source'],
                                   capture_method='offscreen_native_ui_and_equivalent_native_surface_composite',
                                   placeholder=event['placeholder'])
            recorder.event('offscreen_frame_observed', observer='offscreen_native', native_id=event['native_id'],
                           frame_id=frame['frame_id'], frame_status=frame['status'], segment_ids=segment_ids,
                           segment_id=segment_ids[0] if segment_ids else None, observed_empty_body=not segment_ids,
                           interaction_id=event.get('interaction_id', recorder.current_interaction_id), desktop_presented=False)
            return frame

        for line in process.stdout:
            if not line.startswith(PREFIX):
                log.append(line)
                continue
            event = json.loads(line[len(PREFIX):])
            kind = event.pop('kind')
            recorder.append('native/ai4vn/observations.jsonl', {'kind': kind, **event})
            if kind == 'asset':
                path = Path(event['path'])
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                output_id = _output_id(gateway, digest, event.get('call_id'), event.get('candidate_index', 0))
                parent = assets.get(event.get('native_path')) if event.get('parent_sha256') else None
                if parent and parent.get('file_sha256') != event['parent_sha256']:
                    parent = None
                # References reuse the actual prior asset, not a new library
                # identity that would sever its original candidate lineage.
                if event.get('role') == 'reference' and event.get('native_path') in assets:
                    continue
                asset = recorder.asset(path, origin=event.get('origin', 'generated'), output_id=output_id,
                                       parent_asset_id=parent and parent['asset_id'], role=event.get('role'),
                                       native_source={'file': event.get('native_path'), 'operation_id': event.get('operation_id'),
                                                      'call_id': event.get('call_id'), 'candidate_index': event.get('candidate_index')})
                if event.get('native_path'):
                    assets[event['native_path']] = asset
            elif kind == 'character':
                entity = event['entity_id']
                version = recorder.character(entity, event['version'], native_source=event['native_source'],
                                             reference_asset_ids=[assets[p]['asset_id'] for p in event.get('reference_paths', []) if p in assets])
                versions[entity] = version
            elif kind == 'story_unit_available':
                recorder.event('native_story_unit_available', observer='adapter_backend', native_id=event['native_unit'],
                               **{key: value for key, value in event.items() if key != 'native_unit'})
            elif kind == 'story':
                segment = recorder.story(event['text'], speaker=event.get('speaker'), kind=event.get('text_kind', 'narration'),
                                         native_source=event['native_source'], revision_id=event['revision_id'],
                                         native_id=event['native_id'], source_call_ids=event.get('source_call_ids'))
                if not segment:
                    answer({'stop': recorder.scope_reached})
                    continue
                observe_frame(event, [segment['segment_id']])
                answer({'stop': recorder.scope_reached})
            elif kind == 'choice_request':
                menu_frame = observe_frame(event, [])
                recorder.event('choice_available', observer='offscreen_native', native_id=event['native_id'],
                               options=event['options'], interaction_id=event['interaction_id'], frame_id=menu_frame['frame_id'])
                indices = policy.get('choice_indices', [0])
                selected = indices[min(choices_seen, len(indices)-1)]
                if not 0 <= selected < len(event['options']):
                    recorder.choice(event['options'], native_source=event['native_source'], native_id=event['native_id'])
                    answer({'stop': True, 'error': 'choice_policy_out_of_range'})
                else:
                    delay = policy.get('reading_delay_seconds', 0)
                    if delay:
                        recorder.event('simulated_reader_delay_started', interaction_id=event['interaction_id'])
                        time.sleep(delay)
                        recorder.event('simulated_reader_delay_finished', interaction_id=event['interaction_id'])
                    answer({'selected_index': selected})
            elif kind == 'choice_committed':
                recorder.choice(event['options'], selected_index=event['selected_index'],
                                native_source=event['native_source'], native_id=event['native_id'])
                recorder.event('choice_selected', observer='offscreen_native', interaction_id=event['interaction_id'],
                               selected_index=event['selected_index'], native_choices_made=event['native_choices_made'])
                choices_seen += 1
            elif kind == 'failure':
                errors.append(event)
                recorder.error(event['code'], event['message'], stage=event.get('stage'), source='native_ai4vn')
            elif kind == 'visual_review':
                asset = _asset_for_path(assets, event.get('native_path'))
                if asset and asset['file_sha256'] != event.get('asset_sha256'):
                    asset = None
                recorder.append('images/native_reviews.jsonl', {**event, 'asset_id': asset and asset['asset_id']})
            elif kind == 'image_native_result' and not event['native_returned_image']:
                error = {'code': 'native_image_generation_failed', 'message': 'Native Artist returned no image bytes'}
                errors.append(error)
                recorder.error(**error, operation_id=event.get('operation_id'), call_id=event.get('call_id'))
            elif kind == 'image_selected_for_game':
                asset = assets.get(event.get('native_path'))
                recorder.append('images/native_selections.jsonl', {**event, 'asset_id': asset and asset['asset_id']})
                if event.get('rejected_but_used'):
                    error = {'code': 'native_image_review_exhausted_used_last', 'message': 'Native workflow retained its last rejected image'}
                    errors.append(error)
                    recorder.error(**error, native_source=event)
            elif kind == 'asset_resolution':
                recorder.append('visuals/native_asset_resolutions.jsonl', event)
                if not event['available']:
                    error = {'code': 'native_renderer_asset_missing', 'message': 'Native renderer could not resolve requested ' + event['role']}
                    errors.append(error)
                    recorder.error(**error, native_source=event)
            elif kind == 'finished':
                final = event
            else:
                recorder.event('native_' + kind, observer='native_subprocess_collected', **event)
        status = process.wait()
        if status or final is None:
            if not errors:
                error = {'code': 'native_exit', 'message': 'native full CLI exited ' + str(status)}
                errors.append(error)
                recorder.error(**error)
            return {'stop_reason': 'native_error', 'native_ended': False, 'errors': errors, 'native_exit_code': status}
        return {**final, 'errors': errors, 'native_exit_code': status, 'choice_count': choices_seen,
                'visual_observer': 'offscreen_native', 'desktop_presented': False}
    except Exception as exc:
        code = getattr(exc, 'code', 'ai4vn_driver_error')
        recorder.error(code, str(exc), source='ai4vn_external_driver')
        return {'stop_reason': 'native_error', 'native_ended': False, 'errors': [{'code': code, 'message': str(exc)}]}
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if process is not None:
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
        if handle is not None:
            recorder.save_bytes('native/ai4vn/full.stdout.log', redact_evidence(_redact(''.join(log))).encode('utf-8'))
            for path in Path(handle['native_dir']).rglob('*.log'):
                path.write_text(redact_evidence(path.read_text(encoding='utf-8', errors='replace')), encoding='utf-8')
            adapter.close(handle)

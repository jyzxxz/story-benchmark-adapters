"""AI4VisualNovel v2.1 first-artifact adapter; the native CLI owns generation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import types

BASELINE = '0faf120244d175866eea3813f053281f5689ab19'


def _read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate_json_key:{key}')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique)


def _write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def _redact(text):
    for name, value in os.environ.items():
        if len(value) >= 8 and re.search(r'api.?key|token|secret|password|cookie', name, re.I):
            text = text.replace(value, '[REDACTED]')
    return re.sub(r'(?i)Bearer\s+[^\s,;]+', 'Bearer [REDACTED]', text)


def _native_parser(repo):
    """Load the frozen native parser in a private package; no GUI imports."""
    package_name = '_ai4vn_parser_' + hashlib.sha256(str(repo).encode()).hexdigest()[:12]
    if package_name + '.data' not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(repo / 'game_engine')]
        sys.modules[package_name] = package
        spec = importlib.util.spec_from_file_location(package_name + '.data', repo / 'game_engine' / 'data.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        original_bytecode_setting = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = original_bytecode_setting
    return sys.modules[package_name + '.data'].StoryParser


def extract_first_visible(repo: Path, story_path: Path, design: dict, native_source='native/ai4vn/data/story.txt'):
    """Use native parsed lines; stop before any unimplemented control transfer.

    Source pointers refer to physical story.txt lines plus native node/line index.
    We do not reconstruct prose from a graph, design document, or summary.
    """
    parser = _native_parser(Path(repo))
    nodes = {}
    current = None
    for physical, raw in enumerate(Path(story_path).read_text(encoding='utf-8').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('这里为您生成') or line.startswith('=== End'):
            continue
        match = re.match(r'===\s*Node:\s*(.+?)\s*===', line, re.I)
        if match:
            current = match.group(1).strip()
            # Native parse_story replaces an earlier repeated node with the last.
            nodes[current] = []
        elif current:
            parsed = parser._parse_line(line)
            if parsed:
                nodes[current].append((parsed, physical))
    segments, choices, issues = [], [], []
    if 'root' not in nodes:
        return {'segments': [], 'choices': [], 'generation_issue_codes': ['missing_entry_node'], 'stop_reason': 'missing_entry_node'}
    lines = nodes['root']
    stop_reason = 'entry_node_end'
    index = 0
    while index < len(lines):
        parsed, physical = lines[index]
        kind = parsed['type']
        pointer = {'node_id': 'root', 'parsed_line_index': index, 'line': physical}
        if kind in ('if', 'else', 'endif', 'jump'):
            stop_reason = 'control_flow_boundary'
            issues.append('control_flow_not_executed')
            break
        if kind in ('choice_start', 'choice_option'):
            cursor = index + (1 if kind == 'choice_start' else 0)
            while cursor < len(lines) and lines[cursor][0]['type'] == 'choice_option':
                item, source_line = lines[cursor]
                choices.append({'choice_id': f'choice-{len(choices)+1:03d}', 'text': item['text'],
                                'native_target': item['target'], 'native_source': native_source,
                                'native_pointer': {'node_id': 'root', 'parsed_line_index': cursor, 'line': source_line}})
                cursor += 1
            if choices:
                stop_reason = 'first_choice'
                break
            # Native UI skips an empty choice_start and continues.
        elif kind in ('narrator', 'dialogue'):
            speaker = parsed.get('speaker')
            if speaker == '主角':
                speaker = '我'
            elif speaker:
                speaker = next((c.get('name', speaker) for c in design.get('characters', []) if c.get('id', '').upper() == speaker.upper()), {'PROTAGONIST': '我', 'NARRATOR': '旁白'}.get(speaker.upper(), speaker))
            segments.append({'segment_id': f'p{len(segments)+1:03d}',
                             'kind': 'narration' if kind == 'narrator' else 'dialogue',
                             'speaker': speaker, 'text': parsed['text'],
                             'native_source': native_source, 'native_pointer': pointer})
        index += 1
    if not segments:
        issues.append('empty_prose')
    return {'segments': segments, 'choices': choices, 'generation_issue_codes': issues, 'stop_reason': stop_reason}


class AI4VNAdapter:
    system = 'ai4vn'

    def __init__(self, config: dict):
        self.config = dict(config)
        if not config.get('repo_path'):
            raise ValueError('repo_path_required:point_to_the_frozen_AI4VisualNovel_source')
        self.repo = Path(config['repo_path']).resolve()
        self.python = str(config.get('python_executable', sys.executable))
        self.launcher = Path(__file__).resolve().parents[2] / 'native_shims' / 'ai4vn' / 'launcher.py'

    def preflight(self, bundle_dir: Path) -> dict:
        checks, errors = [], []
        bundle_dir = Path(bundle_dir).resolve()
        try:
            shared = (bundle_dir / 'shared_task.txt').read_bytes()
            if not shared or shared.decode('utf-8').strip() != shared.decode('utf-8'):
                raise ValueError('shared_task_empty_or_outer_whitespace')
            if shared != (bundle_dir / 'payloads' / 'requirements.txt').read_bytes():
                raise ValueError('payload_mismatch')
            _read_json(bundle_dir / 'case.json')
            payload = _read_json(bundle_dir / 'payloads' / 'ai4vn.json')
            if not isinstance(payload.get('character_count'), int) or payload['character_count'] < 1:
                raise ValueError('invalid_character_count')
            if not (bundle_dir / 'opening.txt').is_file():
                raise ValueError('missing_opening')
            checks.append('input_files_and_payload_equal')
        except (OSError, ValueError) as error:
            errors.append(str(error))
        try:
            if not self.launcher.is_file():
                raise ValueError('external_launcher_missing')
            if self.config.get('expected_commit', BASELINE) != BASELINE:
                raise ValueError('external_adapter_requires_pristine_baseline')
            from story_benchmark.provenance import verify_repository
            provenance = verify_repository(self.repo, BASELINE, self.config)
            self._source_files()
            checks.extend(provenance['checks'])
            errors.extend(provenance['errors'])
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            errors.append('native_repository_invalid:' + str(error))
        try:
            result = subprocess.run([self.python, '-c', 'import importlib.util,json; print(json.dumps({m:importlib.util.find_spec(m) is not None for m in ["openai","PIL","rembg","dotenv","jsonschema","yaml"]}))'], capture_output=True, text=True, timeout=15, check=True)
            dependencies = json.loads(result.stdout)
            missing = [name for name, present in dependencies.items() if not present]
            if missing:
                errors.append('missing_dependencies:' + ','.join(missing))
            else:
                checks.append('native_python_dependencies_present')
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append('python_unavailable:' + str(error))
        if self.config.get('live'):
            for name in ('max_calls', 'max_output_tokens', 'max_input_chars', 'timeout_seconds'):
                if type(self.config.get(name)) not in (int, float) or self.config[name] <= 0:
                    errors.append('missing_positive_budget:' + name)
                elif name != 'timeout_seconds' and type(self.config[name]) is not int:
                    errors.append('integer_budget_required:' + name)
            if self.config.get('text_provider', 'openai') != 'openai':
                errors.append('provider_http_budget_unsupported')
            if not self.config.get('model'):
                errors.append('model_not_frozen')
            key_name = self.config.get('api_key_env', 'OPENAI_API_KEY')
            if not os.getenv(key_name):
                errors.append('missing_auth_environment:' + key_name)
        return {'ok': not errors, 'checks': checks, 'errors': errors, 'native_integration': 'not_run'}

    def prepare(self, bundle_dir: Path, run_dir: Path) -> dict:
        result = self.preflight(bundle_dir)
        if not result['ok']:
            raise ValueError('preflight_failed:' + json.dumps(result['errors']))
        bundle_dir, run_dir = Path(bundle_dir).resolve(), Path(run_dir).resolve()
        native_dir = run_dir / 'native' / 'ai4vn'
        native_dir.mkdir(parents=True, exist_ok=False)
        files = self._source_files()
        source_dir = native_dir / 'source'
        source_dir.mkdir()
        hashes = {}
        for relative in files:
            source = self.repo / relative
            destination = source_dir / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError('native_source_not_regular:' + relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
        requirements = native_dir / 'requirements.txt'
        requirements.write_bytes((bundle_dir / 'shared_task.txt').read_bytes())
        shared = requirements.read_text(encoding='utf-8')
        trace_dir = Path(self.config.get('trace_dir') or run_dir / 'trace').resolve()
        trace_dir.mkdir(parents=True, exist_ok=True)
        payload = _read_json(bundle_dir / 'payloads' / 'ai4vn.json')
        handle = {'bundle_dir': str(bundle_dir), 'run_dir': str(run_dir), 'native_dir': str(native_dir),
                  'requirements_file': str(requirements), 'trace_dir': str(trace_dir),
                  'shared_task_sha256': hashlib.sha256(shared.encode()).hexdigest(),
                  'character_count': payload['character_count'], 'stages': {}, 'adapter_status': 'prepared',
                  'source_dir': str(source_dir), 'source_hashes': hashes, 'source_mode': 'pristine_copy_external_launcher'}
        self._verify_sources(handle, 'prepared')
        _write_json(native_dir / 'adapter-state.json', handle)
        return handle

    def _source_files(self):
        if self.config.get('source_lock'):
            lock = _read_json(self.config['source_lock'])
            rows = [row for row in lock['systems'].values() if row['base_commit'] == BASELINE]
            if len(rows) != 1:
                raise ValueError('unknown_source_lock_system')
            if rows[0].get('adapted_commit') != BASELINE:
                raise ValueError('source_lock_is_not_pristine_baseline')
            files = list(rows[0]['files'])
        else:
            files = subprocess.check_output(['git', '-C', str(self.repo), 'ls-tree', '-r', '--name-only', BASELINE], text=True).splitlines()
        for relative in files:
            path = Path(relative)
            if path.is_absolute() or '..' in path.parts or path.name == '.env':
                raise ValueError('unsafe_native_source_file:' + relative)
        return files

    def _verify_sources(self, handle, phase):
        changed = []
        for relative, expected in handle['source_hashes'].items():
            for root_name, root in (('baseline', self.repo), ('run_copy', Path(handle['source_dir']))):
                path = root / relative
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    changed.append(root_name + ':' + relative)
        evidence = {'source_mode': 'pristine_copy_external_launcher', 'baseline_commit': BASELINE,
                    'phase': phase, 'source_files_verified': len(handle['source_hashes']),
                    'unchanged': not changed, 'changed': changed}
        _write_json(Path(handle['trace_dir']) / ('source-integrity-ai4vn-' + phase + '.json'), evidence)
        if changed:
            raise RuntimeError('native_source_changed:' + ','.join(changed))
        return evidence

    def _env(self, handle, stage):
        retained = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE')
        environment = {name: os.environ[name] for name in retained if name in os.environ}
        environment.update({'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
                            'BENCH_NATIVE_ROOT': handle['source_dir'],
                            'TEXT_PROVIDER': self.config.get('text_provider', 'openai'),
                            'MODEL': self.config['model'], 'OPENAI_API_KEY': os.environ[self.config.get('api_key_env', 'OPENAI_API_KEY')],
                            'OPENAI_BASE_URL': self.config.get('model_base_url', self.config.get('base_url', 'https://api.openai.com/v1')),
                            'BENCH_RUN_ID': str(self.config.get('root_run_id') or Path(handle['run_dir']).name),
                            'BENCH_TRACE_DIR': handle['trace_dir'], 'BENCH_OPERATION_ID': 'ai4vn.' + stage,
                            'BENCH_MAX_CALLS': str(self.config['max_calls']),
                            'BENCH_MAX_OUTPUT_TOKENS': str(self.config['max_output_tokens']),
                            'BENCH_MAX_INPUT_CHARS': str(self.config['max_input_chars']), 'BENCH_ALLOW_LIVE': '1'})
        return environment

    def _save_state(self, handle):
        _write_json(Path(handle['native_dir']) / 'adapter-state.json', handle)

    def _run_stage(self, handle, stage, command):
        previous = handle['stages'].get(stage)
        if previous == 'completed':
            return
        if previous:
            raise RuntimeError('delivery_unknown:inspect_existing_native_artifacts_before_resume')
        handle['stages'][stage] = 'running'
        self._save_state(handle)
        process = subprocess.Popen(command, cwd=handle['source_dir'], env=self._env(handle, stage),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding='utf-8', errors='replace', start_new_session=True)
        output = ''
        try:
            output, _ = process.communicate(timeout=self.config['timeout_seconds'])
            if process.returncode:
                handle['stages'][stage] = 'failed'
                raise RuntimeError(f'native_exit:{stage}:{process.returncode}')
            handle['stages'][stage] = 'completed'
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            handle['stages'][stage] = 'delivery_unknown'
            raise RuntimeError('delivery_unknown:native_timeout')
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
                handle['stages'][stage] = 'delivery_unknown'
            raise
        finally:
            (Path(handle['native_dir']) / f'{stage}.stdout.log').write_text(_redact(output), encoding='utf-8')
            self._save_state(handle)

    def generate_first_artifact(self, handle: dict) -> dict:
        if not self.config.get('live'):
            raise RuntimeError('live_generation_not_enabled')
        received = Path(handle['requirements_file']).read_bytes()
        if hashlib.sha256(received).hexdigest() != handle['shared_task_sha256']:
            raise RuntimeError('prepared_input_changed')
        self._verify_sources(handle, 'before_generation')
        self._run_stage(handle, 'design', [self.python, str(self.launcher), '--native-root', handle['source_dir'], '--', '--mode', 'design',
                                         '--requirements-file', handle['requirements_file'], '--character-count', str(handle['character_count'])])
        native_data = Path(handle['source_dir']) / 'data'
        for name in ('game_design.json', 'story_graph.json'):
            if not (native_data / name).is_file() or not _read_json(native_data / name):
                raise RuntimeError('native_artifact_missing_or_empty:' + name)
        self._run_stage(handle, 'script', [self.python, str(self.launcher), '--native-root', handle['source_dir'], '--', '--mode', 'script'])
        if not (native_data / 'story.txt').is_file():
            raise RuntimeError('native_artifact_missing:story.txt')
        self._verify_sources(handle, 'after_generation')
        handle['adapter_status'] = 'generated'
        self._save_state(handle)
        return {'native_artifacts': [str(native_data / name) for name in ('game_design.json', 'story_graph.json', 'story.txt')], 'native_integration': 'ran'}

    def export_first_artifact(self, handle: dict) -> dict:
        data = Path(handle['source_dir']) / 'data'
        return extract_first_visible(Path(handle['source_dir']), data / 'story.txt', _read_json(data / 'game_design.json'),
                                     str((data / 'story.txt').relative_to(handle['run_dir'])))

    def close(self, handle: dict) -> None:
        # Retain every failed artifact; only sanitize textual diagnostic logs.
        for path in Path(handle['native_dir']).rglob('*.log'):
            path.write_text(_redact(path.read_text(encoding='utf-8', errors='replace')), encoding='utf-8')
        self._verify_sources(handle, 'closed')
        self._save_state(handle)

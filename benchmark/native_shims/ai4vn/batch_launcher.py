"""v4-only external observations and native pygame playback (no source edits)."""
from __future__ import annotations

import argparse
import contextvars
import functools
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import uuid

PREFIX = 'BENCH_AI4VN_V4 '
_lock = threading.Lock()
_operation = contextvars.ContextVar('native_operation', default=None)
_image_call = contextvars.ContextVar('image_call', default=None)
_calls = contextvars.ContextVar('native_calls', default=None)
_unit = contextvars.ContextVar('native_unit', default=None)
_node_script_calls = {}
_reviews = {}
_stage = 'initialization'


def emit(kind, **fields):
    record = {'kind': kind, 'native_monotonic_ns': time.monotonic_ns(), 'native_pid': os.getpid(),
              'stage': _stage, **fields}
    with _lock:
        sys.stdout.write(PREFIX + json.dumps(record, ensure_ascii=False, default=str) + '\n')
        sys.stdout.flush()


def ask(kind, **fields):
    emit(kind, **fields)
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError('recorder_control_channel_closed')
    return json.loads(line)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(path, *, origin='generated', role=None, parent_sha256=None):
    path = Path(path).resolve()
    if not path.is_file():
        return None
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    copy = Path(os.environ['BENCH_V4_CAPTURE']) / ('asset-' + sha + path.suffix)
    copy.parent.mkdir(parents=True, exist_ok=True)
    if not copy.exists():
        copy.write_bytes(content)
    emit('asset', path=str(copy), native_path=str(path), sha256=sha, origin=origin, role=role,
         parent_sha256=parent_sha256, call_id=_image_call.get(), candidate_index=0 if _image_call.get() else None,
         operation_id=_operation.get())
    return sha


def install_routes_and_observers():
    """Only replace client endpoint/model configuration; native prompts stay intact."""
    import openai
    from openai.resources.chat.completions import Completions
    from agents.artist_agent import ArtistAgent
    from agents.actor_agent import ActorAgent
    from agents.writer_agent import WriterAgent
    from agents.designer_agent import DesignerAgent
    from agents.producer_agent import ProducerAgent
    from workflow import WorkflowController

    original_init = openai.OpenAI.__init__

    @functools.wraps(original_init)
    def initialize(client, *args, **kwargs):
        original_init(client, *args, **kwargs)
        def sending(request):
            # Local gateway provenance headers are stripped before forwarding.
            request.headers['X-Benchmark-Native-Stage'] = _stage
            # Non-ASCII native IDs remain in observation JSON; an optional
            # diagnostic HTTP header must never break a valid native request.
            if _unit.get() and _unit.get().isascii():
                request.headers['X-Benchmark-Native-Task-Id'] = _unit.get()
            if _operation.get():
                request.headers['X-Benchmark-Native-Operation-Id'] = _operation.get()
        def received(response):
            call_id = response.headers.get('x-benchmark-call-id')
            path = response.request.url.path
            if '/images/' in path and response.status_code < 400:
                _image_call.set(call_id)
            if _calls.get() is not None and call_id:
                _calls.get().append(call_id)
            emit('http_receipt', call_id=call_id, operation_id=_operation.get(),
                 provider_request_id=response.headers.get('x-request-id'), http_status=response.status_code,
                 native_endpoint_path=path)
        client._client.event_hooks.setdefault('request', []).append(sending)
        client._client.event_hooks.setdefault('response', []).append(received)

    openai.OpenAI.__init__ = initialize
    vision_clients = threading.local()
    original_create = Completions.create

    @functools.wraps(original_create)
    def create(resource, *args, **kwargs):
        multimodal = any(isinstance(m.get('content'), list) and any(p.get('type') == 'image_url' for p in m['content'])
                         for m in kwargs.get('messages', []))
        if multimodal:
            if not hasattr(vision_clients, 'client'):
                vision_clients.client = openai.OpenAI(api_key=os.environ['OPENAI_API_KEY'],
                                                     base_url=os.environ['BENCH_V4_VISION_URL'])
            resource = vision_clients.client.chat.completions
            kwargs = {**kwargs, 'model': os.environ['BENCH_V4_VISION_MODEL']}
        return original_create(resource, *args, **kwargs)

    Completions.create = create
    original_artist_init = ArtistAgent._initialize_client

    @functools.wraps(original_artist_init)
    def initialize_artist(self):
        # Original initialization and failure semantics still run, with the
        # explicit image endpoint supplied instead of the text endpoint.
        self.base_url = os.environ['BENCH_V4_IMAGE_URL']
        self.api_key = os.environ['OPENAI_API_KEY']
        return original_artist_init(self)

    ArtistAgent._initialize_client = initialize_artist

    def observe_method(cls, name):
        original = getattr(cls, name)
        @functools.wraps(original)
        def wrapped(self, *args, **kwargs):
            op = cls.__name__ + '.' + name + ':' + uuid.uuid4().hex
            token = _operation.set(op)
            call_token = _calls.set([])
            emit('operation_started', operation_id=op, method=cls.__name__ + '.' + name)
            try:
                result = original(self, *args, **kwargs)
                emit('operation_finished', operation_id=op, method=cls.__name__ + '.' + name,
                     source_call_ids=list(_calls.get()), native_unit=_unit.get(), result=result)
                if cls is WriterAgent and name == 'synthesize_script' and _unit.get():
                    _node_script_calls[_unit.get()] = list(_calls.get()) or None
                return result
            except Exception as exc:
                emit('operation_failed', operation_id=op, method=cls.__name__ + '.' + name,
                     source_call_ids=list(_calls.get()), error_type=type(exc).__name__, message=str(exc))
                raise
            finally:
                _operation.reset(token)
                _calls.reset(call_token)
        setattr(cls, name, wrapped)

    for cls, names in [(DesignerAgent, ['generate_game_outline', 'generate_story_graph_from_outline']),
                       (ProducerAgent, ['critique_game_outline', 'critique_story_graph']),
                       (ActorAgent, ['critique_visual', 'generate_expression_description', 'perform_plot']),
                       (WriterAgent, ['synthesize_script', 'split_node_into_plots', 'decide_next_speaker', 'summarize_story']),
                       (ArtistAgent, ['generate_character_images', 'generate_background', 'generate_title_image'])]:
        for name in names:
            observe_method(cls, name)

    original_image = ArtistAgent._call_image_api
    @functools.wraps(original_image)
    def image_api(self, prompt, reference_image_paths=None):
        _image_call.set(None)
        refs = [{'path': str(Path(p).resolve()), 'sha256': snapshot(p, origin='library', role='reference')}
                for p in (reference_image_paths or []) if p and Path(p).is_file()]
        emit('image_anchor', operation_id=_operation.get(), prompt_before_native_truncation=prompt,
             references=refs, native_prompt_transform='ArtistAgent._call_image_api:prompt[:1000]')
        result = original_image(self, prompt, reference_image_paths)
        emit('image_native_result', operation_id=_operation.get(), call_id=_image_call.get(),
             selected_candidate_index=0 if result else None,
             returned_bytes_sha256=hashlib.sha256(result).hexdigest() if result else None,
             native_returned_image=bool(result))
        return result
    ArtistAgent._call_image_api = image_api

    original_save = ArtistAgent._save_image
    @functools.wraps(original_save)
    def save_image(self, data, filepath):
        result = original_save(self, data, filepath)
        snapshot(filepath, role='native_raw_image')
        return result
    ArtistAgent._save_image = save_image

    original_remove = ArtistAgent._remove_background
    @functools.wraps(original_remove)
    def remove_background(self, filepath):
        before = digest(filepath)
        result = original_remove(self, filepath)
        snapshot(filepath, origin='derived', role='native_character_cutout', parent_sha256=before)
        emit('image_derivation', method='native_rembg', file=str(filepath), before_sha256=before,
             after_sha256=digest(filepath), changed=digest(filepath) != before)
        return result
    ArtistAgent._remove_background = remove_background

    original_review = ActorAgent.critique_visual
    @functools.wraps(original_review)
    def visual_review(self, image_path, expression='neutral', reference_image_path=None, **kwargs):
        refs = [digest(reference_image_path)] if reference_image_path and Path(reference_image_path).is_file() else []
        emit('character', entity_id=self.character_info.get('id', self.name), version=self.character_info,
             reference_sha256=refs, reference_paths=[str(Path(reference_image_path).resolve())] if refs else [],
             native_source={'method': 'ActorAgent.critique_visual', 'image_path': str(image_path)})
        result = original_review(self, image_path, expression, reference_image_path, **kwargs)
        emit('visual_review', entity_id=self.character_info.get('id', self.name), expression=expression,
             native_path=str(Path(image_path).resolve()), asset_sha256=digest(image_path),
             native_decision='PASS' if result == 'PASS' else 'REVISE', feedback=result)
        _reviews[(self.character_info.get('id', self.name), expression)] = result
        return result
    ActorAgent.critique_visual = visual_review

    original_node = WorkflowController._generate_node_script
    @functools.wraps(original_node)
    def node_script(self, node_id, *args, **kwargs):
        token = _unit.set(node_id)
        try:
            return original_node(self, node_id, *args, **kwargs)
        finally:
            _unit.reset(token)
    WorkflowController._generate_node_script = node_script

    original_select_image = WorkflowController._generate_expression_with_critique
    @functools.wraps(original_select_image)
    def select_image(self, actor, expression, **kwargs):
        result = original_select_image(self, actor, expression, **kwargs)
        review = _reviews.get((actor.character_info.get('id', actor.name), expression))
        emit('image_selected_for_game', native_path=str(Path(result).resolve()) if result else None,
             expression=expression, entity_id=actor.character_info.get('id', actor.name),
             final_native_review=review, rejected_but_used=bool(result and review and review != 'PASS'))
        return result
    WorkflowController._generate_expression_with_critique = select_image

    original_save_node = WorkflowController._save_node_story
    @functools.wraps(original_save_node)
    def save_node(self, node_id, content):
        result = original_save_node(self, node_id, content)
        from agents.config import PathConfig
        from game_engine.data import StoryParser
        path = Path(PathConfig.STORY_FILE)
        sha = digest(path)
        snapshot_path = Path(os.environ['BENCH_V4_CAPTURE']) / ('story-revision-' + sha + '.txt')
        snapshot_path.write_bytes(path.read_bytes())
        parsed = StoryParser.parse_story('\n=== Node: ' + node_id + ' ===\n' + content)
        readable = [line['text'] for line in parsed.get(node_id, []) if line.get('type') in ('dialogue', 'narrator')]
        emit('story_unit_available', native_unit=node_id, native_source={'file': str(snapshot_path), 'sha256': sha},
             readable_text=readable, source_call_ids=_node_script_calls.get(node_id))
        return result
    WorkflowController._save_node_story = save_node

    original_actors = WorkflowController._initialize_actors
    @functools.wraps(original_actors)
    def actors(self):
        result = original_actors(self)
        for index, char in enumerate(self.game_design.get('characters', [])):
            emit('character', entity_id=char.get('id', char['name']), version=char,
                 native_source={'file': 'native/ai4vn/source/data/game_design.json', 'pointer': '/characters/' + str(index)})
        return result
    WorkflowController._initialize_actors = actors


def playback(native_root):
    import pygame
    from game_engine.manager import GameManager
    from game_engine.scenes import DialogueScene
    from game_engine.config import Colors, SCREEN_WIDTH, SCREEN_HEIGHT
    from game_engine.data import StoryParser

    paths = {}
    character_surfaces = {}
    original_load, original_scale = pygame.image.load, pygame.transform.scale
    def load(file, *args, **kwargs):
        surface = original_load(file, *args, **kwargs)
        paths[id(surface)] = str(Path(file).resolve())
        return surface
    def scale(surface, *args, **kwargs):
        scaled = original_scale(surface, *args, **kwargs)
        if id(surface) in paths:
            paths[id(scaled)] = paths[id(surface)]
        return scaled
    pygame.image.load, pygame.transform.scale = load, scale
    original_character_load = DialogueScene.load_character_image
    def character_load(self, character_id, emotion='neutral'):
        surface = original_character_load(self, character_id, emotion)
        if surface is not None:
            character_surfaces[id(surface)] = character_id
        emit('asset_resolution', role='character', entity_id=character_id, requested_expression=emotion,
             native_path=paths.get(id(surface)) if surface is not None else None,
             available=surface is not None)
        return surface
    DialogueScene.load_character_image = character_load
    original_background_load = DialogueScene.load_background_image
    def background_load(self, name):
        surface = original_background_load(self, name)
        emit('asset_resolution', role='background', requested_background=name,
             native_path=paths.get(id(surface)) if surface is not None else None,
             available=surface is not None)
        return surface
    DialogueScene.load_background_image = background_load
    story = native_root / 'data/story.txt'
    revision = digest(story)
    line_map = {}
    node, index = None, 0
    import re
    for number, raw in enumerate(story.read_text().splitlines(), 1):
        text = raw.strip()
        match = re.match(r'===\s*Node:\s*(.+?)\s*===', text, re.I)
        if match:
            node, index = match.group(1).strip(), 0
        elif node and text and not text.startswith(('这里为您生成', '=== End')) and StoryParser._parse_line(text):
            line_map[(node, index)] = number
            index += 1
    game = GameManager()
    parsed_file = Path(os.environ['BENCH_V4_CAPTURE']) / 'native-parsed-story.json'
    parsed_file.write_text(json.dumps(game.parsed_story, ensure_ascii=False, indent=2))
    if not game.parsed_story.get('root'):
        raise ValueError('native_script_missing_playable_root')
    game.start_story()
    frames = 0

    def capture_frame(scene):
        nonlocal frames
        scene.draw(game.screen)
        pygame.display.flip()
        frames += 1
        capture = Path(os.environ['BENCH_V4_CAPTURE'])
        ui = capture / f'frame-{frames:06d}.ui.png'
        clean_path = capture / f'frame-{frames:06d}.clean.png'
        pygame.image.save(game.screen, ui)
        clean = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        layers = []
        if scene.current_background:
            clean.blit(scene.current_background, (0, 0))
            if id(scene.current_background) in paths:
                layers.append({'role': 'background', 'path': paths[id(scene.current_background)]})
        else:
            clean.fill(Colors.BG_MORNING)
        character_ids = []
        if scene.current_character_image is not None:
            surface = scene.current_character_image
            clean.blit(surface, ((SCREEN_WIDTH - surface.get_width()) // 2, SCREEN_HEIGHT - surface.get_height()))
            if id(surface) in paths:
                path = paths[id(surface)]
                layers.append({'role': 'character', 'path': path})
                if id(surface) in character_surfaces:
                    character_ids.append(character_surfaces[id(surface)])
        pygame.image.save(clean, clean_path)
        return {'layers': layers, 'character_ids': character_ids, 'clean_path': str(clean_path),
                'ui_path': str(ui), 'placeholder': scene.current_background is None}

    try:
        while isinstance(game.current_scene, DialogueScene):
            scene = game.current_scene
            node = game.game_state.current_node_id
            key = (node, scene.index)
            native_id = node + ':' + str(scene.index)
            pointer = '/' + node.replace('~', '~0').replace('/', '~1') + '/' + str(scene.index)
            source = {'file': str(parsed_file.relative_to(Path(os.environ['BENCH_V4_ROOT']))), 'pointer': pointer,
                      'original_file': 'native/ai4vn/source/data/story.txt', 'line': line_map.get(key)}
            if scene.in_choice:
                options = [{'id': str(i), 'label': c.get('text', ''), 'target': c.get('target'), 'raw_native': c}
                           for i, c in enumerate(scene.choice_options)]
                interaction = 'choice-' + uuid.uuid4().hex
                reply = ask('choice_request', options=options, native_id=native_id,
                            native_source={**source, 'revision_id': revision, 'native_ui_kind': 'choice_menu'},
                            interaction_id=interaction, **capture_frame(scene))
                if reply.get('stop'):
                    if reply.get('error'):
                        raise ValueError(reply['error'])
                    return {'stop_reason': 'reading_window', 'native_ended': False}
                selected = reply['selected_index']
                if type(selected) is not int or not 0 <= selected < len(options):
                    raise ValueError('choice_policy_out_of_range')
                target = options[selected]['target']
                # The native manager prints and stalls on absent targets. Do not
                # manufacture a route from the graph or use another node.
                if target and target not in game.parsed_story:
                    raise ValueError('native_choice_target_missing:' + target)
                scene.make_choice(selected)
                emit('choice_committed', options=options, selected_index=selected, native_id=native_id,
                     native_source=source, interaction_id=interaction,
                     native_choices_made=list(game.game_state.choices_made))
                continue
            instruction = scene.script_lines[scene.index]
            if instruction.get('type') not in ('dialogue', 'narrator'):
                raise ValueError('native_playback_stalled:' + native_id)
            # Use the real fast-forward input handler, then the original draw.
            if not scene.finished_typing:
                scene.process_input(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
            reply = ask('story', text=scene.full_text, text_kind='dialogue' if instruction['type'] == 'dialogue' else 'narration',
                        speaker=scene.current_speaker, native_id=node, native_instruction_id=native_id,
                        native_source={**source, 'pointer': pointer + '/text'}, revision_id=revision,
                        source_call_ids=_node_script_calls.get(node), **capture_frame(scene))
            if reply.get('stop'):
                return {'stop_reason': 'reading_window', 'native_ended': False}
            scene.process_input(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
        return {'stop_reason': 'native_end', 'native_ended': True}
    finally:
        pygame.quit()


def main():
    global _stage
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-root', required=True)
    parser.add_argument('--requirements-file', required=True)
    parser.add_argument('--character-count', required=True)
    args = parser.parse_args()
    native_root = Path(args.native_root).resolve()
    os.chdir(native_root)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(native_root))
    spec = importlib.util.spec_from_file_location('_ai4vn_full_native_main', native_root / 'main.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    install_routes_and_observers()
    original_resolve = native.resolve_design_inputs
    def resolve(arguments):
        result = original_resolve(arguments)
        emit('received_input', received_task=result[0], boundary='native_requirements_reader', oc_count=len(result[1]))
        return result
    native.resolve_design_inputs = resolve
    Path(os.environ['BENCH_V4_CAPTURE']).mkdir(parents=True, exist_ok=True)
    try:
        for stage in ('design', 'script', 'render'):
            _stage = stage
            emit('stage_started', native_mode=stage)
            sys.argv = [str(native_root / 'main.py'), '--mode', stage]
            if stage == 'design':
                sys.argv += ['--requirements-file', args.requirements_file, '--character-count', args.character_count]
            native.main()
            emit('stage_finished', native_mode=stage)
            if stage == 'design':
                design = json.loads((native_root / 'data/game_design.json').read_text())
                if not design.get('story_graph'):
                    raise ValueError('native_design_incomplete')
            if stage == 'script' and not (native_root / 'data/story.txt').is_file():
                raise ValueError('native_script_missing')
        _stage = 'playback'
        emit('finished', **playback(native_root))
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            code = 'native_interrupted'
        elif isinstance(exc, SystemExit):
            code = 'native_exit'
        else:
            code = str(exc).split(':', 1)[0] if str(exc).startswith('native_') else 'native_full_workflow_error'
        emit('failure', code=code, message=str(exc), exception_type=type(exc).__name__)
        traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

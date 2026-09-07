"""Compile once; native envelopes never normalize the compiled string."""
import json
from pathlib import Path
from .io import BenchmarkError, atomic_json, atomic_write, read_json, safe_child, sha256
from .contracts import validate_contracts, canonical_case_hash

MARKERS = ('<<<SHARED_TASK_V2_BEGIN>>>', '<<<SHARED_TASK_V2_END>>>',
           '<<<BRIEF_BEGIN>>>', '<<<BRIEF_END>>>', '<<<OPENING_BEGIN>>>', '<<<OPENING_END>>>')
REQUIRED = {'case_id', 'case_version', 'review_status', 'title', 'language', 'player_name',
            'character_names', 'visual_style', 'brief_file', 'opening_file', 'prefix_file',
            'profile', 'scope_map', 'decisions', 'provenance'}
OPTIONAL = {'review_file', 'target_new_visible_chars', 'output_boundary', 'input_contract', 'output_contract'}
PROFILES = {'TEXT_CONTINUATION_DEV', 'TEXT_BRANCHING', 'FULL_VN'}


def validate_output_boundary(case):
    boundary = case.get('output_boundary')
    if boundary is None:
        return None
    if not isinstance(boundary, dict) or set(boundary) != {'kind', 'decision_id', 'include_options', 'execute_choice'}:
        raise BenchmarkError('invalid_output_boundary')
    if (boundary['kind'] != 'first_choice' or boundary['include_options'] is not True
            or boundary['execute_choice'] is not False
            or not case.get('decisions') or boundary['decision_id'] != case['decisions'][0]['id']):
        raise BenchmarkError('unsupported_output_boundary')
    return boundary


def normalize(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').strip()


def _clean_source(text):
    if any(marker in text for marker in MARKERS):
        raise BenchmarkError('forged_shared_boundary')
    if '\x00' in text or '\ufeff' in text:
        raise BenchmarkError('invalid_source_character')
    if not normalize(text):
        raise BenchmarkError('empty_source')
    return normalize(text)


def load_case(case_file, allow_pilot=False):
    case_file = Path(case_file).resolve()
    root = case_file.parent.parent
    case = read_json(case_file)
    if not isinstance(case, dict) or REQUIRED - set(case):
        raise BenchmarkError('missing_case_fields: ' + ','.join(sorted(REQUIRED - set(case))))
    if set(case) - REQUIRED - OPTIONAL:
        raise BenchmarkError('unknown_case_fields: ' + ','.join(sorted(set(case) - REQUIRED - OPTIONAL)))
    for key in REQUIRED - {'character_names', 'scope_map', 'decisions', 'provenance'}:
        if not isinstance(case[key], str) or not case[key].strip():
            raise BenchmarkError('invalid_case_field: ' + key)
    if case['profile'] not in PROFILES or case['language'] != 'zh-CN':
        raise BenchmarkError('unsupported_profile_or_language')
    names = case['character_names']
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n.strip() for n in names) or len(names) != len(set(names)):
        raise BenchmarkError('invalid_character_names')
    if case['player_name'] not in names:
        raise BenchmarkError('player_not_declared')
    if case['review_status'] not in {'pilot', 'approved'}:
        raise BenchmarkError('invalid_review_status')
    if case['review_status'] != 'approved' and not (allow_pilot and (case['profile'] == 'TEXT_CONTINUATION_DEV'
            or case['profile']=='FULL_VN' and case.get('output_contract',{}).get('version')=='4.0')):
        raise BenchmarkError('unapproved_case_requires_explicit_dev_mode')
    scopes = case['scope_map']
    if not isinstance(scopes, dict) or not scopes or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in scopes.items()):
        raise BenchmarkError('invalid_scope_map')
    decisions = case['decisions']
    if not isinstance(decisions, list) or len(decisions) != 2:
        raise BenchmarkError('two_decisions_required')
    ids = []
    for decision in decisions:
        if not isinstance(decision, dict) or set(decision) != {'id', 'options'} or not isinstance(decision['id'], str):
            raise BenchmarkError('invalid_decision')
        ids.append(decision['id'])
        if not isinstance(decision['options'], list) or len(decision['options']) != 2:
            raise BenchmarkError('invalid_options')
        option_ids = []
        for option in decision['options']:
            if not isinstance(option, dict) or set(option) != {'id', 'text'} or any(not isinstance(x, str) or not x.strip() for x in option.values()):
                raise BenchmarkError('invalid_option')
            option_ids.append(option['id'])
        if len(set(option_ids)) != 2:
            raise BenchmarkError('duplicate_option_id')
    if len(set(ids)) != 2:
        raise BenchmarkError('duplicate_decision_id')
    validate_output_boundary(case)
    validate_contracts(case)
    provenance = case['provenance']
    if not isinstance(provenance, dict) or not isinstance(provenance.get('source_sha256'), dict):
        raise BenchmarkError('source_hashes_required')
    texts, hashes = {}, {}
    for key in ('brief', 'opening', 'prefix'):
        path = safe_child(root, case[key + '_file'])
        raw = path.read_bytes()
        expected = provenance['source_sha256'].get(key)
        if expected != sha256(raw):
            raise BenchmarkError('source_hash_mismatch: ' + key)
        texts[key] = _clean_source(raw.decode('utf-8'))
        hashes[key] = sha256(raw)
    # Check every field which enters the common task, including metadata.
    _clean_source(json.dumps({k: case[k] for k in ('title', 'player_name', 'character_names', 'visual_style', 'scope_map')}, ensure_ascii=False))
    for decision in decisions:
        for option in decision['options']:
            if option['text'] not in texts['brief']:
                raise BenchmarkError('decision_not_in_brief')
    if case['review_status'] == 'approved':
        if 'review_file' not in case:
            raise BenchmarkError('approval_record_required')
        review = read_json(safe_child(root, case['review_file']))
        if review.get('status') != 'approved' or not review.get('reviewer') or not review.get('reviewed_at'):
            raise BenchmarkError('approval_record_invalid')
        if review.get('source_sha256') != hashes or review.get('case_sha256') != sha256(case_file.read_bytes()):
            raise BenchmarkError('approval_hash_mismatch')
    return case, texts, hashes


def render(case, texts):
    boundary = validate_output_boundary(case)
    v3 = validate_contracts(case)
    v4 = case.get('output_contract',{}).get('version')=='4.0'
    scope_items = sorted(case['scope_map'].items()) if v3 else case['scope_map'].items()
    scope = '\n\n'.join(k + '：\n' + v for k, v in scope_items)
    parameters = '\n'.join([
        '【公共参数】', '标题：' + case['title'], '语言：简体中文',
        '玩家角色：' + case['player_name'] + '。',
        '第二人称“你”或第一人称“我”均指' + case['player_name'] + '。允许沿用原生叙事视角，不另加一个新玩家角色。',
        ('本任务故事角色总名单（含玩家，不另增故事角色）：' if v3 else '已有核心人物：')
        + '、'.join(case['character_names']) + '，共' + str(len(case['character_names'])) + '名。',
        '视听风格：' + case['visual_style']])
    if case['profile'] == 'TEXT_CONTINUATION_DEV':
        execution = '【开发任务范围】\n本次用于文本续写接入调试。保留原生文字规划、人物、记忆、审核和脚本步骤；保留原题视听氛围，不请求真实图片、配音或音乐。'
        if case.get('target_new_visible_chars') is not None:
            raise BenchmarkError('dev_profile_has_hard_length_target')
    else:
        execution = '【任务范围】\n' + case['profile']
        if case.get('target_new_visible_chars') is not None:
            execution += '\n新增正文目标：' + str(case['target_new_visible_chars'])
    if boundary:
        execution += ('\n本轮玩家可见输出边界：第一次选择 ' + boundary['decision_id']
                      + '，包含该选择的两个选项，然后停止；不执行任一选项。'
                      + '\n该边界不禁止原生内部规划、审核或预生成后续脚本；后续产物独立保存，不拼入当前可见路径。')
    if v4:
        execution += ('\n本轮生成真实图文，保留原生规划、审核、修订、素材生成、预取与后续脚本预生成。'
                      '\n共同阅读范围：固定开头之后新增可见正文达到 '+str(case['output_contract']['window_chars'])
                      +' 个 Unicode 字符后，外部程序在相同的完整句边界停止阅读。固定开头、内部规划和未选预览不计新增正文。'
                      '\n不要求全篇结局或收束。观察范围之内仍应呈现题目要求的选择和后果，外部程序按统一策略实际选择原生选项。'
                      '\n只在实际选择后将相应后果作为已发生剧情，互斥分支不拼成同一路线。'
                      '\n生成保持原生格式，外部程序统一保存正文、选择、真实画面与调用记录。')
    elif v3:
        execution += ('\n统一返回范围：从固定开头之后，到第一次选择 '
                      + case['output_contract']['decision_id'] + ' 的未选择状态。'
                      + '\n固定开头已经停在选择处时，可直接给出原生选项，不强制另写过渡正文。'
                      + '\n选项可以使用原生标题、标识或候选卡片；原生附带的选择后预览单列为未发生内容，不执行选择。'
                      + '\n内部规划、审核和后续分支预生成照原生流程进行；返回格式由外部程序统一，不要求模型改用公共JSON格式。')
    return '\n\n'.join([MARKERS[0], texts['prefix'], parameters, execution,
        '<<<BRIEF_BEGIN>>>\n' + texts['brief'] + '\n<<<BRIEF_END>>>',
        ('【共同语义解释】\n' if boundary or v3 else '【共同时间解释】\n') + scope,
        '<<<OPENING_BEGIN>>>\n' + texts['opening'] + '\n<<<OPENING_END>>>',
        '【续写说明】\n固定开头中的事件已经发生。正文从最后的情境继续，保持人物知识与物品状态；不要重新开局，不要替玩家提前执行尚未选择的关键行动。使用原生输出格式。', MARKERS[1]])


def payloads(case, shared):
    return {
        'if_line': {'title': case['title'], 'characters': [], 'story_start': '', 'story_end': '', 'extra_requirements': shared},
        'infiplot': {'worldSetting': shared, 'styleGuide': case['visual_style'], 'playerName': case['player_name'], 'language': case['language']},
        'ai4vn': {'character_count': len(case['character_names']), 'character_count_semantics': 'native exact design count including protagonist; core count mapping is a recorded native constraint', 'requirements_file': 'requirements.txt'}}


def compile_case(case_file, out, allow_pilot=False):
    case, texts, hashes = load_case(case_file, allow_pilot)
    shared = render(case, texts)
    native = payloads(case, shared)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    atomic_write(out / 'shared_task.txt', shared)
    atomic_write(out / 'opening.txt', texts['opening'])
    atomic_json(out / 'case.json', case)
    if case['review_status'] == 'approved':
        atomic_json(out / 'review.json', read_json(safe_child(Path(case_file).resolve().parent.parent, case['review_file'])))
    for name, value in native.items():
        atomic_json(out / 'payloads' / (name + '.json'), value)
    atomic_write(out / 'payloads/requirements.txt', shared)
    v3 = validate_contracts(case)
    if v3:
        for name in ('brief', 'opening', 'prefix'):
            atomic_write(out/'sources'/(name+'.txt'), safe_child(Path(case_file).resolve().parent.parent,case[name+'_file']).read_bytes())
    files = {str(p.relative_to(out)): sha256(p.read_bytes()) for p in out.rglob('*') if p.is_file()}
    manifest = {'schema_version': '2.1', 'case_id': case['case_id'], 'case_sha256': sha256(Path(case_file).read_bytes()),
                'profile': case['profile'], 'review_status': case['review_status'], 'source_sha256': hashes,
                'shared_sha256': sha256(shared), 'shared_chars': len(shared), 'shared_bytes': len(shared.encode('utf-8')),
                'files': files, 'payload_check': 'passed', 'native_integration': 'not_run'}
    if case.get('output_boundary'):
        manifest['output_boundary'] = case['output_boundary']
    if v3:
        manifest.update(input_contract=case['input_contract'], output_contract=case['output_contract'],
                        canonical_case_sha256=canonical_case_hash(case))
    atomic_json(out / 'manifest.json', manifest)
    return verify_bundle(out)


def verify_bundle(out):
    out = Path(out)
    manifest = read_json(out / 'manifest.json')
    required_files = {'shared_task.txt', 'opening.txt', 'case.json', 'payloads/if_line.json',
                      'payloads/infiplot.json', 'payloads/ai4vn.json', 'payloads/requirements.txt'}
    if not isinstance(manifest.get('files'), dict) or not required_files.issubset(manifest['files']):
        raise BenchmarkError('incomplete_bundle_manifest')
    for relative, expected in manifest['files'].items():
        if sha256(safe_child(out, relative).read_bytes()) != expected:
            raise BenchmarkError('bundle_hash_mismatch: ' + relative)
    shared = (out / 'shared_task.txt').read_bytes().decode('utf-8')
    if shared != shared.strip() or '\r' in shared or sha256(shared) != manifest['shared_sha256']:
        raise BenchmarkError('invalid_shared_task')
    values = [read_json(out / 'payloads/if_line.json')['extra_requirements'],
              read_json(out / 'payloads/infiplot.json')['worldSetting'],
              (out / 'payloads/requirements.txt').read_bytes().decode('utf-8')]
    if any(value != shared for value in values):
        raise BenchmarkError('payload_mismatch')
    case = read_json(out / 'case.json')
    if validate_contracts(case):
        if (manifest.get('input_contract') != case['input_contract']
                or manifest.get('output_contract') != case['output_contract']
                or manifest.get('canonical_case_sha256') != canonical_case_hash(case)):
            raise BenchmarkError('bundle_contract_or_case_mismatch')
        actual_files = {str(p.relative_to(out)) for p in out.rglob('*') if p.is_file() and p != out/'manifest.json'}
        if actual_files != set(manifest['files']):
            raise BenchmarkError('bundle_file_set_mismatch')
        texts = {}
        for name in ('brief', 'opening', 'prefix'):
            source = out/'sources'/(name+'.txt')
            if 'sources/'+name+'.txt' not in manifest['files'] or sha256(source.read_bytes()) != manifest['source_sha256'][name]:
                raise BenchmarkError('bundle_source_bytes_mismatch:'+name)
            texts[name] = _clean_source(source.read_text(encoding='utf-8'))
        if render(case, texts) != shared or texts['opening'] != (out/'opening.txt').read_text(encoding='utf-8'):
            raise BenchmarkError('bundle_rendered_task_mismatch')
    boundary = validate_output_boundary(case)
    if manifest.get('output_boundary') != boundary:
        raise BenchmarkError('bundle_output_boundary_mismatch')
    if any(manifest.get(key) != case.get(key) for key in ('case_id', 'profile', 'review_status')):
        raise BenchmarkError('bundle_case_metadata_mismatch')
    if manifest.get('source_sha256') != case.get('provenance', {}).get('source_sha256'):
        raise BenchmarkError('bundle_source_metadata_mismatch')
    if manifest['review_status'] == 'approved':
        if 'review.json' not in manifest['files']:
            raise BenchmarkError('approval_record_required')
        review = read_json(out / 'review.json')
        if (review.get('status') != 'approved' or not review.get('reviewer') or not review.get('reviewed_at')
                or review.get('source_sha256') != manifest.get('source_sha256')
                or review.get('case_sha256') != manifest.get('case_sha256')):
            raise BenchmarkError('approval_hash_mismatch')
    expected = payloads(case, shared)
    for name in expected:
        if read_json(out / 'payloads' / (name + '.json')) != expected[name]:
            raise BenchmarkError('payload_metadata_mismatch: ' + name)
    opening = (out / 'opening.txt').read_bytes().decode('utf-8')
    if ('<<<OPENING_BEGIN>>>\n' + opening + '\n<<<OPENING_END>>>') not in shared:
        raise BenchmarkError('missing_opening')
    return {'payload_check': 'passed', 'native_integration': 'not_run', 'shared_sha256': sha256(shared), 'shared_chars': len(shared)}

#!/usr/bin/env python3
"""Export the action-open pilot suite. No services, model calls or content approval.

The original eval30 sources remain immutable historical provenance. Only the
active brief, opening and prefix enter native payloads and review constraints.
"""
from __future__ import annotations
import argparse
import copy
from pathlib import Path
import re
import sys

import eval30 as legacy
from eval30 import ROOT, digest
from story_benchmark.compiler import compile_case, verify_bundle
from story_benchmark.io import atomic_json, atomic_write, read_json, safe_child

CATALOG = ROOT / 'benchmark/suites/eval30_open/catalog.json'


def catalog_sources(catalog_path: Path = CATALOG, repo: Path = ROOT) -> tuple[dict, dict[str, str], bytes]:
    meta = read_json(catalog_path)
    if (meta.get('schema_version') != 'open-eval30.1'
            or meta.get('review_status') != 'pilot'
            or meta.get('decision_policy') != 'native_generated'):
        raise ValueError('Unsupported action-open pilot catalog')
    for relative, expected in meta['legacy_file_sha256'].items():
        if digest(safe_child(repo, relative).read_bytes()) != expected:
            raise ValueError('Legacy provenance changed: ' + relative)
    base, _, _ = legacy.catalog_sources(repo/'benchmark/suites/eval30/catalog.json', repo)
    if base['source_sha256'] != meta['source_sha256'] or base['suite_version'] != meta['base_suite_version']:
        raise ValueError('Original source identity mismatch')
    sources = {}
    for role in ('prompts', 'prefix', 'changes'):
        relative = meta[role+'_file']
        content = safe_child(catalog_path.parent, relative).read_bytes()
        if digest(content) != meta['file_sha256'].get(relative):
            raise ValueError('Open source hash mismatch: ' + relative)
        sources[role] = content
    matches = re.findall(
        r'^## ([A-Z]+(?:-[A-Z]+)?-\d{2})｜([^\n]+)\n\n'
        r'### 故事设定\n```text\n(.*?)\n```\n\n### 固定开头\n```text\n(.*?)\n```',
        sources['prompts'].decode('utf-8'), flags=re.M | re.S)
    parsed = {ident: (title, brief, opening) for ident, title, brief, opening in matches}
    ids = [row['source_prompt_id'] for row in base['cases']]
    expected = meta.get('expected_shared_sha256', {})
    if (len(matches) != 30 or len(parsed) != 30 or set(parsed) != set(ids)
            or set(expected) != set(ids)
            or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in expected.values())):
        raise ValueError('Thirty unique prompts and frozen shared hashes are required')
    rows, briefs = [], {}
    for old in base['cases']:
        ident = old['source_prompt_id']
        title, brief, opening = parsed[ident]
        if title != old['title'] or not brief.strip() or not opening.strip():
            raise ValueError('Title/source identity mismatch: ' + ident)
        if re.search(r'C[12]|第[一二]次选择[：:]|第[一二三]章|前两章|面前是一项尚未确认的选择[：:]', brief+'\n'+opening):
            raise ValueError('Prescribed action/phase residue: ' + ident)
        styles = re.findall(r'^视觉/音频：(.*)$', brief, flags=re.M)
        if len(styles) != 1:
            raise ValueError('Exactly one public visual style is required: ' + ident)
        row = {k: copy.deepcopy(old[k]) for k in ('source_prompt_id', 'title', 'genre', 'player_name', 'character_names')}
        row.update(case_id=ident+'-V4-OPEN02', opening=opening, visual_style=styles[0],
                   shared_sha256=expected[ident], original_source_sha256=old['source_sha256'],
                   parent_case_id=old['case_id'], parent_shared_sha256=old['shared_sha256'])
        rows.append(row)
        briefs[ident] = brief
    common = copy.deepcopy(base['common_case'])
    common.update(case_version=meta['case_version'], decision_policy='native_generated', decisions=[])
    scope = {
        '01_前文与知识': '固定开头中的观察、对白和状态已经发生。其他背景是作者设定，不自动成为角色已知信息；新的获知、物品转移及状态变化须有实际事件依据。人物主张、文件指令、计划与预测不自动成为已执行行动或必然结局。',
        '02_场与时间': '第一场是固定开头所在事件及紧随其后的同一连续事件；后续以实质时空或事件目标转换划分，不以原生菜单、章节标题、换行或模型请求计数。没有与菜单编号绑定的阶段，也不要求所有系统到达相同事件节点。时限从开头状态持续计算，不得凭空重置。',
        '03_角色名单': base['common_scope']['02_角色名单与背景对象'],
        '04_行动与后果': '本题没有预设关键行动。选项内容和后续发展由原生系统生成，适配器只按公开位置策略选择实际菜单。无须跨系统匹配行动语义，未选预览不是已经发生的事实。条件性世界规则与视觉要求不强迫对应行动发生。',
    }
    return {**meta, 'cases': rows, 'common_case': common, 'common_scope': scope}, briefs, sources['prefix']


def build_case(data: dict, row: dict, brief: str, prefix: bytes, catalog_path: Path) -> dict:
    case = copy.deepcopy(data['common_case'])
    for key in ('case_id', 'title', 'player_name', 'character_names', 'visual_style'):
        case[key] = copy.deepcopy(row[key])
    ident = row['source_prompt_id']
    case.update(scope_map=copy.deepcopy(data['common_scope']), brief_file=f'briefs/{ident}.txt',
                opening_file=f'openings/{ident}.txt', prefix_file='profiles/open_actions.txt')
    case['provenance'] = {
        'source_prompt_id': ident, 'source_file': data['source_file'],
        'parent_case_id': row['parent_case_id'], 'parent_shared_sha256': row['parent_shared_sha256'],
        'source_attachment_sha256': data['source_sha256'],
        'original_source_sha256': row['original_source_sha256'],
        'source_revision': data['inspected_commit'], 'suite_version': data['suite_version'],
        'active_catalog_sha256': digest(catalog_path.read_bytes()),
        'active_source_files': data['file_sha256'],
        'brief_source': 'Action-open revision in PROMPTS.md; original v1 remains archived unchanged.',
        'opening_source': 'Disclosed neutral last paragraphs plus SCI-FI-03 first-paragraph menu removal; see CHANGES.md.',
        'scope_source': 'No menu-bound phases. Existing cast boundary retained; see open-suite migration notes.',
        'source_sha256': {'brief': digest(brief.encode('utf-8')),
                          'opening': digest(row['opening'].encode('utf-8')), 'prefix': digest(prefix)},
    }
    return case


def expand_suite(out: Path, catalog_path: Path = CATALOG, repo: Path = ROOT) -> dict:
    data, briefs, prefix = catalog_sources(catalog_path, repo)
    out = Path(out).absolute()
    out.mkdir(parents=True, exist_ok=False)
    atomic_write(out/'CHANGES.md', safe_child(catalog_path.parent, data['changes_file']).read_bytes())
    atomic_write(out/'history/original_v1.md', safe_child(repo, data['source_file']).read_bytes())
    rows, rendered = [], ['# 30 题完整公共输入：不预设关键行动\n\nPilot 候选。每次只提交一题；原生格式保持不变。\n']
    for row in data['cases']:
        ident = row['source_prompt_id']
        case = build_case(data, row, briefs[ident], prefix, catalog_path)
        for key, content in [('brief', briefs[ident]), ('opening', row['opening']), ('prefix', prefix)]:
            atomic_write(out/case[key+'_file'], content)
        relative = 'cases/'+case['case_id']+'.json'
        atomic_json(out/relative, case)
        bundle = out/'compiled'/case['case_id']
        result = compile_case(out/relative, bundle, allow_pilot=True)
        verify_bundle(bundle)
        if result['shared_sha256'] != row['shared_sha256']:
            raise ValueError('Frozen open task changed: ' + ident)
        rows.append({'source_prompt_id': ident, 'case_id': case['case_id'], 'genre': row['genre'],
                     'title': row['title'], 'case_file': relative, 'review_status': 'pilot',
                     'decision_policy': 'native_generated', 'shared_sha256': result['shared_sha256']})
        rendered.append(f'\n## {ident}｜{row["title"]}\n\n```text\n{(bundle/"shared_task.txt").read_text(encoding="utf-8")}\n```\n')
    manifest = {'suite_version': data['suite_version'], 'case_count': len(rows), 'cases': rows,
                'decision_policy': 'native_generated', 'source_sha256': data['source_sha256'],
                'catalog_sha256': digest(catalog_path.read_bytes()), 'active_source_files': data['file_sha256'],
                'paid_model_calls': 0, 'native_integration': 'not_run'}
    atomic_json(out/'suite_manifest.json', manifest)
    atomic_write(out/'PROMPTS_30_COMPILED.md', ''.join(rendered))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'work/open-eval30-preview')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    if args.list:
        data, _, _ = catalog_sources()
        for row in data['cases']:
            print(f'{row["source_prompt_id"]:14} {row["title"]}')
    else:
        result = expand_suite(args.out)
        print(f'{result["case_count"]}/30 action-open inputs verified; zero model calls. Output: {args.out.absolute()}')
        print('Still pilot, not approved. See docs/OPEN_ACTION_EXPERIMENTS.md before generation.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}. Partial output retained; do not overwrite a frozen experiment.', file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Derive a versioned pilot suite without prescribed actions; no model calls.
The legacy sources are immutable provenance, not operative instructions.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re

from eval30 import ROOT, CATALOG, catalog_sources, digest
from story_benchmark.compiler import compile_case, verify_bundle
from story_benchmark.io import atomic_json, atomic_write, read_json, safe_child

RULES = ROOT / 'benchmark/suites/eval30/open-actions.json'


def revise_brief(original: str, replacements: list) -> tuple[str, list]:
    lines = original.splitlines()
    removed = [line for line in lines if re.match(r'^第[一二]次选择：', line)]
    if len(removed) != 2:
        raise ValueError('Expected exactly two original prescribed-choice lines')
    text = '\n'.join(line for line in lines if line not in removed)
    changes = [{'before': line, 'after': '', 'reason': 'remove prescribed action options'} for line in removed]
    for before, after in replacements:
        if not before or text.count(before) != 1:
            raise ValueError('Ambiguous/missing versioned replacement: ' + before)
        text = text.replace(before, after, 1)
        changes.append({'before': before, 'after': after, 'reason': 'remove prescribed action/stage dependency'})
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if re.search(r'C[12]|第[一二三四五六七八九十0-9]+章|前两章|第[一二]次选择：', text):
        raise ValueError('Prescribed choice/stage survived brief derivation')
    return text, changes


def expand_suite(out: Path, rules_path: Path = RULES,
                 catalog_path: Path = CATALOG, repo: Path = ROOT) -> dict:
    data, briefs, _ = catalog_sources(catalog_path, repo)
    rules = read_json(rules_path)
    if rules.get('schema_version') != 'open-actions.1' or rules['parent_suite'] != data['suite_version']:
        raise ValueError('Unknown open-actions rules or legacy version')
    prefix = safe_child(rules_path.parent, rules['prefix_file']).read_bytes()
    if digest(prefix) != rules['prefix_sha256']:
        raise ValueError('Open-actions prefix hash mismatch')
    known = {row['source_prompt_id'] for row in data['cases']}
    if set(rules['brief_replacements']) - known:
        raise ValueError('Unknown case in open-actions replacements')
    out = out.absolute()
    out.mkdir(parents=True, exist_ok=False)
    prefix_path = 'profiles/shared_open_actions.txt'
    atomic_write(out/prefix_path, prefix)
    atomic_write(out/'history/original_v1.md', safe_child(repo, data['source_file']).read_bytes())
    atomic_write(out/'history/open-actions.json', rules_path.read_bytes())
    rendered = ['# 30 题完整公共输入：不预设关键行动\n\npilot 候选。每次只提交一题。历史原文不属于当前评分要求。\n']
    rows, changes = [], []
    for row in data['cases']:
        ident = row['source_prompt_id']
        brief, edits = revise_brief(briefs[ident], rules['brief_replacements'].get(ident, []))
        case = copy.deepcopy(data['common_case'])
        for key in ('title', 'player_name', 'character_names', 'visual_style'):
            case[key] = copy.deepcopy(row[key])
        case.update(case_id=ident+'-V4-OPEN01', case_version=rules['case_version'],
                    review_status='pilot', decision_policy='native_generated', decisions=[])
        # Do not propagate old per-case route explanations or the C1/C2 chapter map.
        case['scope_map'] = {**rules['scope_map'],
                            '02_角色名单与背景对象': data['common_scope']['02_角色名单与背景对象']}
        hashes = {'brief': digest(brief.encode()), 'opening': digest(row['opening'].encode()), 'prefix': digest(prefix)}
        case['provenance'] = {**data['common_provenance'], 'source_prompt_id': ident,
            'brief_source': 'Versioned open-actions derivation; original v1 brief is archived separately, NOT verbatim active input.',
            'scope_source': 'Open-actions conditional/initial-state rules; legacy action/stage map is not active.',
            'prefix_source': 'prefix.open-actions.txt; no prescribed action list.',
            'opening_source': row['opening_source'], 'source_sha256': hashes,
            'parent_case_id': row['case_id'], 'parent_shared_sha256': row['shared_sha256'],
            'parent_source_sha256': row['source_sha256'], 'rules_sha256': digest(rules_path.read_bytes())}
        case.update(brief_file=f'briefs/{ident}.txt', opening_file=f'openings/{ident}.txt', prefix_file=prefix_path)
        relative = f'cases/{case["case_id"]}.json'
        atomic_write(out/case['brief_file'], brief)
        atomic_write(out/case['opening_file'], row['opening'])
        atomic_json(out/relative, case)
        target = out/'compiled'/case['case_id']
        check = compile_case(out/relative, target, allow_pilot=True)
        verify_bundle(target)
        shared = (target/'shared_task.txt').read_text(encoding='utf-8')
        if re.search(r'C[12]|第[一二]次选择：|保留原题两组关键选择', shared):
            raise ValueError('Legacy action instruction leaked into common input: '+ident)
        rows.append({'source_prompt_id': ident, 'case_id': case['case_id'], 'genre': row['genre'],
                     'title': row['title'], 'case_file': relative, 'review_status': 'pilot',
                     'shared_sha256': check['shared_sha256'], 'decision_policy': 'native_generated'})
        rendered.append(f'\n## {ident}｜{row["title"]}\n\n```text\n{shared}\n```\n')
        changes.append({'source_prompt_id': ident, 'brief_changes': edits,
                        'old_scope': {**data['common_scope'], '04_本题边界': row['scope']},
                        'new_scope': case['scope_map'], 'opening_changed': False,
                        'characters_changed': False, 'title_changed': False})
    manifest = {'suite_version': rules['suite_version'], 'case_count': len(rows), 'cases': rows,
                'source_sha256': data['source_sha256'], 'rules_sha256': digest(rules_path.read_bytes()),
                'decision_policy': 'native_generated', 'paid_model_calls': 0, 'native_integration': 'not_run'}
    atomic_json(out/'suite_manifest.json', manifest)
    atomic_json(out/'CHANGELOG.json', {'note': rules['change_note'], 'cases': changes})
    atomic_write(out/'PROMPTS_30_COMPILED.md', ''.join(rendered))
    return manifest

#!/usr/bin/env python3
"""Expand the checked-in 30-case sources and use the repository's actual compiler.
No network, native services, model calls or automatic content approval.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'benchmark/suites/eval30/catalog.json'
sys.path.insert(0, str(ROOT / 'benchmark'))
from story_benchmark.compiler import compile_case, verify_bundle
from story_benchmark.io import atomic_json, atomic_write, read_json, safe_child


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog_sources(catalog_path: Path = CATALOG, repo: Path = ROOT) -> tuple[dict, dict[str, str], bytes]:
    data = read_json(catalog_path)
    if data.get('schema_version') != 'eval30.1' or not isinstance(data.get('case_files'),list):
        raise ValueError('Unsupported eval30 catalog')
    if len(set(data['case_files'])) != len(data['case_files']):
        raise ValueError('Duplicate genre source files')
    data['cases'] = [row for name in data['case_files'] for row in read_json(safe_child(catalog_path.parent,name))]
    if len(data['cases']) != 30:
        raise ValueError('Incomplete eval30 catalog')
    source = safe_child(repo, data['source_file']).read_bytes()
    prefix = safe_child(catalog_path.parent, data['prefix_file']).read_bytes()
    if digest(source) != data['source_sha256'] or digest(prefix) != data['prefix_sha256']:
        raise ValueError('Original source/prefix hash mismatch; do not silently change frozen inputs')
    matches = re.findall(r'^### ([A-Z]+(?:-[A-Z]+)?-\d{2})｜[^\n]+\n\n```text\n(.*?)\n```',
                         source.decode('utf-8'), flags=re.M | re.S)
    briefs = dict(matches)
    ids = [row['source_prompt_id'] for row in data['cases']]
    case_ids = [row['case_id'] for row in data['cases']]
    if len(matches) != 30 or len(briefs) != 30 or len(set(ids)) != 30 or len(set(case_ids)) != 30 or set(ids) != set(briefs):
        raise ValueError('Original brief IDs and catalog do not match one-to-one')
    for row in data['cases']:
        for name in (row['source_prompt_id'], row['case_id']):
            if not re.fullmatch(r'[A-Z0-9-]+', name):
                raise ValueError('Unsafe case ID')
        for key, value in [('brief', briefs[row['source_prompt_id']].encode('utf-8')),
                           ('opening', row['opening'].encode('utf-8')), ('prefix', prefix)]:
            if digest(value) != row['source_sha256'][key]:
                raise ValueError(f'Source hash mismatch: {row["source_prompt_id"]}:{key}')
    return data, briefs, prefix


def expand_suite(out: Path, catalog_path: Path = CATALOG, repo: Path = ROOT) -> dict:
    data, briefs, prefix = catalog_sources(catalog_path, repo)
    out = out.absolute()
    out.mkdir(parents=True, exist_ok=False)
    prefix_path = 'profiles/shared_readable_window_v4_30.txt'
    atomic_write(out / prefix_path, prefix)
    rows, rendered = [], ['# 30 题完整公共输入\n\n开发候选，非质量评分结果。每次只提交其中一题，由原生适配器传递。\n']
    for row in data['cases']:
        ident = row['source_prompt_id']
        case = copy.deepcopy(data['common_case'])
        for key in ('case_id', 'title', 'player_name', 'character_names', 'visual_style', 'decisions'):
            case[key] = copy.deepcopy(row[key])
        case['scope_map'] = {**data['common_scope'], '04_本题边界': row['scope']}
        case['provenance'] = {**data['common_provenance'], 'source_prompt_id': ident,
                              'opening_source': row['opening_source'], 'source_sha256': row['source_sha256']}
        case.update(brief_file=f'briefs/{ident}.txt', opening_file=f'openings/{ident}.txt', prefix_file=prefix_path)
        relative = f'cases/{case["case_id"]}.json'
        atomic_write(out / case['brief_file'], briefs[ident])
        atomic_write(out / case['opening_file'], row['opening'])
        atomic_json(out / relative, case)
        target = out / 'compiled' / case['case_id']
        result = compile_case(out / relative, target, allow_pilot=True)
        verify_bundle(target)
        if result['shared_sha256'] != row['shared_sha256']:
            raise ValueError(f'Compiled task differs from supplied v4-30-pilot.1: {ident}')
        rows.append({'source_prompt_id': ident, 'case_id': case['case_id'], 'genre': row['genre'],
                     'title': row['title'], 'case_file': relative, 'review_status': case['review_status'],
                     'shared_sha256': result['shared_sha256']})
        rendered.append(f'\n## {ident}｜{row["title"]}\n\n```text\n{(target/"shared_task.txt").read_text(encoding="utf-8")}\n```\n')
    manifest = {'suite_version': data['suite_version'], 'case_count': len(rows), 'cases': rows,
                'source_sha256': data['source_sha256'], 'catalog_sha256': digest(catalog_path.read_bytes()),
                'genre_source_sha256': {name:digest(safe_child(catalog_path.parent,name).read_bytes()) for name in data['case_files']},
                'paid_model_calls': 0, 'native_integration': 'not_run'}
    atomic_json(out / 'suite_manifest.json', manifest)
    atomic_write(out / 'PROMPTS_30_COMPILED.md', ''.join(rendered))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'work/eval30-preview')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    if args.list:
        data, _, _ = catalog_sources()
        for row in data['cases']:
            print(f'{row["source_prompt_id"]:14} {row["title"]}')
    else:
        result = expand_suite(args.out)
        print(f'{result["case_count"]}/30 inputs compiled and verified; zero model calls. Output: {args.out.absolute()}')
        print('Inputs remain pilot. Native generation requires explicit --allow-pilot, or a human-approved copy.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}. Partial output is retained; use a new directory after fixing the cause.', file=sys.stderr)
        raise SystemExit(2)

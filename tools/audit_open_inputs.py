#!/usr/bin/env python3
"""Read-only audit of an exported open-action suite before paid generation.

Uses the repository compiler's integrity checks. Does not install dependencies,
read API keys, call models, approve content or change any input file.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benchmark'))
from story_benchmark.compiler import load_case, verify_bundle
from story_benchmark.io import read_json, safe_child, sha256

POLICY = 'native_generated'
RESIDUE = re.compile(r'第[一二]次选择[：:]|保留原题两组关键选择')


def audit(suite_root: Path, formal_only: bool = False) -> dict:
    """Verify source-to-bundle-to-payload bindings; return failures explicitly."""
    root = Path(suite_root).resolve()
    catalog = read_json(root / 'suite_manifest.json')
    rows = catalog.get('cases')
    if (not isinstance(rows, list) or not rows
            or not isinstance(catalog.get('suite_version'), str)
            or not catalog['suite_version'].strip()
            or catalog.get('decision_policy') != POLICY):
        raise ValueError('Expected a nonempty exported native_generated suite')
    if type(catalog.get('case_count')) is not int or catalog['case_count'] != len(rows):
        raise ValueError('Suite case_count does not match its case list')
    for field in ('source_prompt_id', 'case_id', 'case_file'):
        values = [row.get(field) if isinstance(row, dict) else None for row in rows]
        if any(not isinstance(v, str) or not v.strip() for v in values) or len(values) != len(set(values)):
            raise ValueError('Missing or duplicate suite identifiers: ' + field)
    results = []
    for row in rows:
        result = {'source_prompt_id': row['source_prompt_id'], 'case_id': row['case_id']}
        try:
            ident = row['case_id']
            if not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
                raise ValueError('Unsafe case identifier')
            source = safe_child(root, row['case_file'])
            case, _, _ = load_case(source, allow_pilot=not formal_only)
            if (case['case_id'] != ident or case.get('decision_policy') != POLICY
                    or case.get('decisions') != [] or row.get('decision_policy') != POLICY):
                raise ValueError('Case must declare native_generated with no prescribed actions')
            if row.get('review_status') != case['review_status']:
                raise ValueError('Suite and source review statuses differ')
            bundle = safe_child(root, 'compiled/' + ident)
            check = verify_bundle(bundle)
            manifest = read_json(bundle / 'manifest.json')
            if read_json(bundle / 'case.json') != case:
                raise ValueError('Source and compiled case differ')
            if manifest.get('case_sha256') != sha256(source.read_bytes()):
                raise ValueError('Compiled case is not bound to the source file bytes')
            if check['shared_sha256'] != row.get('shared_sha256'):
                raise ValueError('Suite and compiled public-task hashes differ')
            if RESIDUE.search((bundle / 'shared_task.txt').read_text(encoding='utf-8')):
                raise ValueError('Literal prescribed-choice instruction remains in the active task')
            result.update(ok=True, shared_sha256=check['shared_sha256'], review_status=case['review_status'])
        except (ValueError, OSError, KeyError, TypeError) as exc:
            result.update(ok=False, error=str(exc))
        results.append(result)
    return {
        'schema_version': 'open-input-audit.1', 'ok': all(row['ok'] for row in results),
        'suite_version': catalog['suite_version'], 'decision_policy': POLICY,
        'case_count': len(rows), 'formal_only': formal_only, 'cases': results,
        'model_calls': 0, 'native_generation': 'not_run', 'content_approval': 'not_performed',
        'limitation': 'Integrity and literal-instruction checks only. Human review must still check semantic action constraints, facts and ambiguity. No story quality or native runtime certification.',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite-root', type=Path, required=True)
    parser.add_argument('--formal-only', action='store_true', help='Reject pilot; require existing valid human approval records')
    parser.add_argument('--out', type=Path, help='Optional NEW JSON report outside the input directory; never overwritten')
    args = parser.parse_args(argv)
    try:
        if args.out:
            target = args.out.resolve()
            if target.is_relative_to(args.suite_root.resolve()) or target.exists():
                raise ValueError('Report must be a new file outside the frozen input directory')
        report = audit(args.suite_root, args.formal_only)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.out:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('x', encoding='utf-8') as stream:
                stream.write(rendered)
        print(rendered, end='')
        return 0 if report['ok'] else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'model_calls': 0}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

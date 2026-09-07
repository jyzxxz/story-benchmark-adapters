#!/usr/bin/env python3
"""Create a new hash-bound copy after HUMAN review, never approve automatically."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'benchmark'))
from story_benchmark.compiler import load_case, render
from story_benchmark.io import atomic_json, atomic_write, read_json, safe_child, sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite-root', type=Path, default=ROOT/'work/open-eval30-preview')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reviewer', required=True, help='Real human reviewer; do not invent an identity')
    parser.add_argument('--confirm-reviewed', action='store_true',
                        help='Affirm review of all briefs, openings, cast, rules, action policy and window')
    args = parser.parse_args()
    src, dst = args.suite_root.resolve(), args.out.resolve()
    if not args.confirm_reviewed or not args.reviewer.strip():
        raise ValueError('Explicit human review and --confirm-reviewed are required')
    if dst.exists() or src == dst or src in dst.parents:
        raise ValueError('Choose a new output directory outside the pilot suite')
    catalog = read_json(src/'suite_manifest.json')
    rows = catalog.get('cases', [])
    if not rows or len({r['case_file'] for r in rows}) != len(rows):
        raise ValueError('A nonempty, unique suite is required')
    for row in rows:
        path = safe_child(src, row['case_file'])
        case, texts, _ = load_case(path, allow_pilot=True)
        if row.get('review_status') != 'pilot' or case['review_status'] != 'pilot':
            raise ValueError('Never overwrite or reapprove an already approved suite')
        if row['case_id'] != case['case_id'] or row.get('shared_sha256') != sha256(render(case, texts)):
            raise ValueError('Source no longer matches exported manifest; version and regenerate before review')
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'compiled', 'VALIDATION.json', 'SHA256SUMS.txt'))
    reviewed_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        path = safe_child(dst, row['case_file'])
        case = read_json(path)
        case['review_status'] = 'approved'
        case['case_version'] = case['case_version'].replace('-pilot.', '-approved.')
        case['review_file'] = f'reviews/{case["case_id"]}.json'
        case['provenance']['approval_note'] = 'Content review asserted by the named human operator; not automated quality certification.'
        atomic_json(path, case)
        atomic_json(safe_child(dst, case['review_file']), {
            'status': 'approved', 'reviewer': args.reviewer.strip(), 'reviewed_at': reviewed_at,
            'source_sha256': case['provenance']['source_sha256'], 'case_sha256': sha256(path.read_bytes()),
            'notes': 'Reviewed full input and action policy. No story generation or quality scoring was performed.'})
        # Re-validate each hash-bound approval, without invoking a model.
        load_case(path, allow_pilot=False)
        row['review_status'] = 'approved'
    catalog['suite_version'] = catalog['suite_version'].replace('-pilot.', '-approved.')
    catalog.update(reviewed_at=reviewed_at, reviewer=args.reviewer.strip())
    atomic_json(dst/'suite_manifest.json', catalog)
    atomic_write(dst/'APPROVAL_NOTE.txt', 'Operator-approved COPY. Narrative documents retain pilot provenance; case files and hash-bound reviews govern compilation.\n')
    print('Approved copy created:', dst)
    print('Use --suite-root PATH without --allow-pilot. No model calls were made.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        raise SystemExit(2)

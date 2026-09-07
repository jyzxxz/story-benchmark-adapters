#!/usr/bin/env python3
"""Create a NEW hash-bound approved copy, only after a HUMAN has reviewed the inputs.
This helper is part of the repository experiment tools. It does not
review content, judge quality, execute models, or change the original pilot suite.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite-root', type=Path, default=Path(__file__).resolve().parents[1]/'work/eval30-preview')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reviewer', required=True, help='Real human reviewer identifier; never invent one')
    parser.add_argument('--confirm-reviewed', action='store_true',
                        help='You affirm that you reviewed all briefs/openings, casts, scopes, choices, and window.')
    args = parser.parse_args()
    src, dst = args.suite_root.resolve(), args.out.resolve()
    if not args.confirm_reviewed or not args.reviewer.strip():
        raise ValueError('Human review and --confirm-reviewed are required. This script is not a substitute for review.')
    if dst.exists() or src == dst or src in dst.parents:
        raise ValueError('Choose a new output directory outside the pilot suite.')
    catalog = json.loads((src/'suite_manifest.json').read_text(encoding='utf-8'))
    if any(row['review_status'] != 'pilot' for row in catalog['cases']):
        raise ValueError('This helper only promotes a pilot copy. Never rewrite a previously approved suite.')
    # Verify source hashes before any approval record is written.
    for row in catalog['cases']:
        case = json.loads((src/row['case_file']).read_text(encoding='utf-8'))
        for key in ('brief','opening','prefix'):
            relative = Path(case[key+'_file'])
            resolved = (src/relative).resolve()
            if relative.is_absolute() or src not in resolved.parents:
                raise ValueError('Source path escapes suite root.')
            if sha(resolved) != case['provenance']['source_sha256'][key]:
                raise ValueError(f'Hash mismatch for {case["case_id"]}:{key}. Version and re-hash changed inputs before review.')
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', 'compiled', 'VALIDATION.json', 'SHA256SUMS.txt'))
    reviewed_at = datetime.now(timezone.utc).isoformat()
    for row in catalog['cases']:
        path = dst/row['case_file']
        case = json.loads(path.read_text(encoding='utf-8'))
        case['review_status'] = 'approved'
        case['case_version'] = case['case_version'].replace('-pilot.', '-approved.', 1)
        case['review_file'] = f'reviews/{case["case_id"]}.json'
        case['provenance']['approval_note'] = 'Content review asserted by the named operator through approve_eval30.py; not an automated quality judgment.'
        dump(path, case)
        dump(dst/case['review_file'], {'status':'approved','reviewer':args.reviewer.strip(),
              'reviewed_at':reviewed_at,'source_sha256':case['provenance']['source_sha256'],
              'case_sha256':sha(path), 'notes':'Human reviewed original brief, added opening/scopes, cast, decisions and window. No generation quality certification.'})
        row['review_status']='approved'
    catalog['suite_version']=catalog['suite_version'].replace('-pilot.', '-approved.', 1)
    catalog['reviewed_at']=reviewed_at
    catalog['reviewer']=args.reviewer.strip()
    dump(dst/'suite_manifest.json',catalog)
    (dst/'APPROVAL_NOTE.txt').write_text('This is an operator-approved COPY. Narrative docs still describe the original pilot provenance; cases and hash-bound reviews govern compilation.\n', encoding='utf-8')
    print('Approved copy created:', dst)
    print('Run tools/experiment.py --suite-root PATH --run WITHOUT --allow-pilot; native compiler validation is still required.')
    return 0

if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:
        print('ERROR:',exc,file=sys.stderr)
        raise SystemExit(2)

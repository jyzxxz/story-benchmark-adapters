"""V4-only sanitization after native clients/services have stopped.

No native request, response, image bytes or immutable system source is changed.
Legacy JSON snapshots and native logs are sanitized before the root is sealed.
"""
from __future__ import annotations

import json
from pathlib import Path

from story_benchmark.io import atomic_write
from story_benchmark.recording import redact_evidence


def sanitize_native_evidence(run_dir, *, frozen_files=()):
    root = Path(run_dir).resolve()
    native = root / 'native'
    if native.is_symlink():
        raise ValueError('native_evidence_directory_must_not_be_symlink')
    frozen = {Path(path).resolve() for path in frozen_files}
    checked, changed, skipped, integrity = 0, [], [], []
    for path in sorted(native.rglob('*')):
        if not path.is_file():
            continue
        if path.suffix not in {'.json', '.jsonl', '.log', '.txt', '.out'} and '.log.' not in path.name:
            continue
        if path.resolve() in frozen:
            skipped.append(str(path.relative_to(root)))
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(native):
            raise ValueError('native_evidence_file_must_not_leave_run')
        original = path.read_text(encoding='utf-8')
        checked += 1
        try:
            if path.suffix == '.json':
                value = json.loads(original)
                if (path.name in {'source-before.json','source-after.json','if_line_source_before.json','if_line_source_after.json'}
                        and isinstance(value,dict) and set(value)<=
                        {'sha256','file_count','files','source_unchanged','before_sha256','after_sha256'}):
                    # Filenames such as api_key_pool.py are provenance keys,
                    # not credential values. These are source hashes only.
                    integrity.append(str(path.relative_to(root)))
                    continue
                clean = redact_evidence(value)
                rendered = json.dumps(clean, ensure_ascii=False, indent=2) if clean != value else original
            elif path.suffix == '.jsonl':
                values = [json.loads(line) for line in original.splitlines() if line.strip()]
                clean = redact_evidence(values)
                rendered = ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in clean) if clean != values else original
            else:
                rendered = redact_evidence(original)
        except json.JSONDecodeError:
            # Interrupted writes remain partial evidence; do not invent JSON.
            rendered = redact_evidence(original)
        if rendered != original:
            atomic_write(path, rendered)
            changed.append(str(path.relative_to(root)))
    return {'mode': 'v4_saved_evidence_only', 'policy': 'common_recording.redact_evidence',
            'checked_text_files': checked, 'changed_files': changed,
            'skipped_frozen_files': skipped,
            'skipped_source_integrity_reports': integrity,
            'live_provider_values_changed': False, 'image_bytes_changed': False}

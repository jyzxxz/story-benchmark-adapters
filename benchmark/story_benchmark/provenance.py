"""Verify frozen source snapshots independently of the surrounding Git layout."""
import os
import json
from pathlib import Path
import re
import subprocess
import tempfile
from .io import BenchmarkError, _pairs, safe_child, sha256

GENERATED_DIRS = {'.git', 'node_modules', '.next', '__pycache__', '.pytest_cache', '.mypy_cache',
                  '.ruff_cache', '.venv', 'venv', '.conda', 'dist', 'build', 'logs', 'coverage', '.turbo'}
GENERATED_FILES = {'.DS_Store', 'tsconfig.tsbuildinfo', 'next-env.d.ts'}


def _relative_path(value):
    if (not isinstance(value, str) or not value or '\\' in value or
            Path(value).is_absolute() or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('invalid_source_patch_relative_path')
    return value


def _plain_file(root, relative):
    relative = _relative_path(relative)
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('source_symlink_forbidden:' + relative)
    return safe_child(root, relative)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _tree_digest(files):
    """Hash the complete relative-path -> SHA256 inventory, not a Git tree ID."""
    return sha256(json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def source_identity(report):
    """Stable content identity to freeze at preparation and compare at execution."""
    if not report.get('ok') or not report.get('checks'):
        raise BenchmarkError('verified_locked_source_identity_required')
    check = report['checks'][0]
    required = ('base_commit', 'source_lock_sha256', 'source_modified', 'native_variant', 'source_tree_sha256')
    if any(key not in check for key in required):
        raise BenchmarkError('verified_locked_source_identity_required')
    result = {'schema_version': 'source-identity.1', **{key: check[key] for key in required},
              'source_patch_manifest_sha256': check.get('source_patch_manifest_sha256'),
              'source_patch_sha256': check.get('source_patch_sha256')}
    if (type(result['source_modified']) is not bool or not _digest(result['source_lock_sha256'])
            or not _digest(result['source_tree_sha256']) or result['source_modified'] and not all(
                _digest(result[key]) for key in ('source_patch_manifest_sha256', 'source_patch_sha256'))):
        raise BenchmarkError('verified_locked_source_identity_required')
    return result


def _patch_inventory(root, system, base_commit, lock_digest, baseline, manifest_path):
    if system != 'if_line':
        raise ValueError('source_patch_only_supported_for_if_line')
    manifest_path = Path(manifest_path).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes, object_pairs_hook=_pairs)
    required = {'schema_version', 'system', 'base_commit', 'baseline_lock_sha256',
                'native_variant', 'patch_file', 'patch_sha256', 'files'}
    if not isinstance(manifest, dict) or set(manifest) != required or manifest['schema_version'] != 'source-patch.1':
        raise ValueError('invalid_source_patch_manifest')
    if manifest['system'] != system or manifest['base_commit'] != base_commit:
        raise ValueError('source_patch_system_or_revision_mismatch')
    if manifest['baseline_lock_sha256'] != lock_digest:
        raise ValueError('source_patch_baseline_lock_mismatch')
    variant = manifest['native_variant']
    if (not isinstance(variant, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', variant)
            or variant.lower() in {'pristine', 'immutable', 'unmodified', 'baseline'}):
        raise ValueError('explicit_modified_native_variant_required')
    patch_path = _plain_file(manifest_path.parent, manifest['patch_file'])
    patch_bytes = patch_path.read_bytes()
    if not _digest(manifest['patch_sha256']) or sha256(patch_bytes) != manifest['patch_sha256']:
        raise ValueError('source_patch_file_hash_mismatch')
    changes = manifest['files']
    if not isinstance(changes, dict) or not changes:
        raise ValueError('source_patch_requires_changed_files')
    expected = dict(baseline)
    seen = set()
    for relative, row in changes.items():
        _relative_path(relative)
        if relative.casefold() in seen:
            raise ValueError('source_patch_duplicate_case_alias')
        seen.add(relative.casefold())
        if not isinstance(row, dict) or set(row) != {'before_sha256', 'after_sha256'} or not _digest(row['after_sha256']):
            raise ValueError('invalid_source_patch_file_record:' + relative)
        if row['before_sha256'] != baseline.get(relative):
            raise ValueError('source_patch_before_hash_mismatch:' + relative)
        if row['before_sha256'] == row['after_sha256']:
            raise ValueError('source_patch_unchanged_file:' + relative)
        # A new spelling must not mask a frozen file on case-insensitive volumes.
        if relative not in baseline and relative.casefold() in {p.casefold() for p in baseline}:
            raise ValueError('source_patch_new_case_alias_forbidden:' + relative)
        path = _plain_file(root, relative)
        if not path.is_file() or sha256(path.read_bytes()) != row['after_sha256']:
            raise ValueError('source_patch_after_hash_mismatch:' + relative)
        expected[relative] = row['after_sha256']
    # Apply only to a disposable copy. The published diff must reproduce the
    # baseline bytes when reversed, rather than merely having a declared hash.
    with tempfile.TemporaryDirectory(prefix='source-patch-verify-') as temp:
        copy_root = Path(temp)
        for relative in changes:
            target = copy_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_plain_file(root, relative).read_bytes())
        result = subprocess.run(['git', '-C', str(copy_root), 'apply', '--reverse', '--whitespace=nowarn', '-'],
                                input=patch_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise ValueError('source_patch_reverse_application_failed')
        copied_paths = list(copy_root.rglob('*'))
        if any(p.is_symlink() for p in copied_paths):
            raise ValueError('source_patch_diff_symlink_forbidden')
        actual = {str(p.relative_to(copy_root)): sha256(p.read_bytes()) for p in copied_paths if p.is_file()}
        before = {p: row['before_sha256'] for p, row in changes.items() if row['before_sha256'] is not None}
        if actual != before:
            raise ValueError('source_patch_diff_does_not_match_declared_files')
    return expected, {'source_modified': True, 'native_variant': variant,
        'source_patch_manifest': str(manifest_path), 'source_patch_manifest_sha256': sha256(manifest_bytes),
        'source_patch_file': str(patch_path), 'source_patch_sha256': sha256(patch_bytes),
        'source_patch_files_verified': len(changes), 'source_patch_reverse_verified': True,
        'source_tree_sha256': _tree_digest(expected)}


def _files(root):
    """Extra source files are drift, including new prompt modules outside the lock."""
    result = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        result.update(str((Path(directory) / d).relative_to(root)) for d in dirs
                      if d not in GENERATED_DIRS and (Path(directory) / d).is_symlink())
        dirs[:] = [d for d in dirs if d not in GENERATED_DIRS]
        for name in files:
            relative = str((Path(directory) / name).relative_to(root))
            if name in GENERATED_FILES or name.endswith(('.pyc', '.pyo')):
                continue
            # Local runtime env is never published and cannot change source hashes.
            if name == '.env' or name.endswith('.local') or name.startswith('.env.') and not name.endswith(('example', 'sample', 'template')):
                continue
            result.add(relative)
    return result


def verify_repository(repo, base_commit, config):
    root = Path(repo).resolve()
    checks, errors = [], []
    variant = {'source_modified': None, 'native_variant': None}
    try:
        if config.get('expected_commit', base_commit) != base_commit:
            raise ValueError('modified_native_revision_forbidden')
        if not root.is_dir():
            raise ValueError('missing_source_directory')
        if config.get('source_lock'):
            lock_path = Path(config['source_lock']).resolve()
            lock_bytes = lock_path.read_bytes()
            lock = json.loads(lock_bytes, object_pairs_hook=_pairs)
            lock_digest = sha256(lock_bytes)
            rows = [(k, v) for k, v in lock['systems'].items() if v['base_commit'] == base_commit]
            if len(rows) != 1:
                raise ValueError('unknown_frozen_system')
            system, item = rows[0]
            if item.get('adapted_commit') != base_commit or item.get('source_modified', False) is not False:
                raise ValueError('modified_native_revision_forbidden')
            expected = item['files']
            if not isinstance(expected, dict) or not expected:
                raise ValueError('empty_source_lock')
            if config.get('source_patch'):
                expected, variant = _patch_inventory(root, system, base_commit, lock_digest,
                                                      expected, config['source_patch'])
            else:
                variant = {'source_modified': False, 'native_variant': 'pristine',
                           'source_tree_sha256': _tree_digest(expected)}
            for relative, digest in expected.items():
                path = _plain_file(root, relative)
                if path.is_symlink() or not path.is_file() or sha256(path.read_bytes()) != digest:
                    raise ValueError('source_hash_mismatch:' + relative)
            extras = _files(root) - set(expected)
            # Git can contain both Docs/ and docs/. On a case-insensitive volume
            # os.walk reports one spelling although both Git paths resolve to
            # the same file. Every expected blob was still checked above.
            case_aliases={}
            for relative in expected:
                case_aliases.setdefault(relative.casefold(),[]).append(relative)
            extras={relative for relative in extras if not any(
                (root/relative).samefile(root/alias)
                for alias in case_aliases.get(relative.casefold(),[]) if (root/alias).exists())}
            if extras:
                raise ValueError('unlocked_source_files:' + ','.join(sorted(extras)[:10]))
            checks.append({'source_lock': str(lock_path), 'base_commit': base_commit,
                           'adapted_commit': None if variant['source_modified'] else item['adapted_commit'],
                           'source_files_verified': len(expected),
                           'source_lock_sha256': lock_digest, **variant})
        else:
            if config.get('source_patch'):
                raise ValueError('source_patch_requires_baseline_lock')
            current = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'], text=True).strip()
            if current != base_commit:
                raise ValueError('expected_commit_mismatch')
            status = subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True)
            if status:
                raise ValueError('uncommitted_source_changes')
            checks.append({'base_commit':base_commit,'adapted_commit':current,'tracked_tree_clean':True})
            variant = {'source_modified': False, 'native_variant': 'pristine'}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        errors.append('source_verification_failed:' + str(exc))
    return {'ok':not errors,'checks':checks,'errors':errors, **variant}

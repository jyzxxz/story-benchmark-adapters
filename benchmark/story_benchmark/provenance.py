"""Verify frozen source snapshots independently of the surrounding Git layout."""
import os
from pathlib import Path
import subprocess
from .io import read_json, safe_child, sha256

GENERATED_DIRS = {'.git', 'node_modules', '.next', '__pycache__', '.pytest_cache', '.mypy_cache',
                  '.ruff_cache', '.venv', 'venv', '.conda', 'dist', 'build', 'logs', 'coverage', '.turbo'}
GENERATED_FILES = {'.DS_Store', 'tsconfig.tsbuildinfo', 'next-env.d.ts'}


def _files(root):
    """Extra source files are drift, including new prompt modules outside the lock."""
    result = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
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
    try:
        if config.get('expected_commit', base_commit) != base_commit:
            raise ValueError('modified_native_revision_forbidden')
        if not root.is_dir():
            raise ValueError('missing_source_directory')
        if config.get('source_lock'):
            lock_path = Path(config['source_lock']).resolve()
            lock = read_json(lock_path)
            rows = [v for v in lock['systems'].values() if v['base_commit'] == base_commit]
            if len(rows) != 1:
                raise ValueError('unknown_frozen_system')
            item = rows[0]
            if item.get('adapted_commit') != base_commit or item.get('source_modified', False) is not False:
                raise ValueError('modified_native_revision_forbidden')
            expected = item['files']
            if not isinstance(expected, dict) or not expected:
                raise ValueError('empty_source_lock')
            for relative, digest in expected.items():
                path = safe_child(root, relative)
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
                           'adapted_commit': item['adapted_commit'], 'source_files_verified': len(expected),
                           'source_lock_sha256': sha256(lock_path.read_bytes())})
        else:
            current = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'], text=True).strip()
            if current != base_commit:
                raise ValueError('expected_commit_mismatch')
            status = subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True)
            if status:
                raise ValueError('uncommitted_source_changes')
            checks.append({'base_commit':base_commit,'adapted_commit':current,'tracked_tree_clean':True})
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        errors.append('source_verification_failed:' + str(exc))
    return {'ok':not errors,'checks':checks,'errors':errors}

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


class BenchmarkError(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data.encode('utf-8') if isinstance(data, str) else data).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError('duplicate_json_key: ' + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_pairs)


def atomic_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def safe_child(root, relative):
    root = Path(root).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise BenchmarkError('path_outside_root: ' + str(relative))
    return candidate


_SECRET = re.compile(r'authorization|cookie|api[-_]?key|password|secret|access[-_]?token|refresh[-_]?token|auth[-_]?token|^token$|^sid$', re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if _SECRET.search(k) and not k.endswith('_env') else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if _SECRET.search(key) and len(secret) >= 8:
                value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)(Bearer\s+)[^\s\"\']+', r'\1[REDACTED]', value)
        value = re.sub(r'(?i)((?:api[-_]?key|access_token|token|sig|signature|key)=)[^&\s]+', r'\1[REDACTED]', value)
        return re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]', value)
    return value

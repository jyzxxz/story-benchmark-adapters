"""Read original local HTTP response observations, independent of CDP caches."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import zlib


class ServerResponseReader:
    def __init__(self, root, node='node'):
        self.root = Path(root)
        self.directory = self.root / 'native/server-wire'
        self.node = node
        self.responses, self.errors, self._seen = {}, {}, {}

    def _decode(self, raw, encoding):
        for kind in reversed([s.strip().lower() for s in encoding.split(',') if s.strip()]):
            if kind == 'identity':
                continue
            if kind == 'gzip':
                raw = gzip.decompress(raw)
            elif kind == 'deflate':
                raw = zlib.decompress(raw)
            elif kind == 'br':
                # Node is already a required native dependency; avoid a new
                # optional Python compression library or content transformation.
                raw = subprocess.run([self.node, '-e', "const z=require('node:zlib');const c=[];process.stdin.on('data',x=>c.push(x));process.stdin.on('end',()=>process.stdout.write(z.brotliDecompressSync(Buffer.concat(c))));"],
                    input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
            else:
                raise ValueError('unsupported_content_encoding:' + kind)
        return raw

    def refresh(self, final=False):
        paths = list(self.directory.glob('*.json'))
        present = {p.stem for p in paths}
        for operation in set(self.responses) - present:
            self.responses.pop(operation, None)
            self.errors[operation + '.json'] = 'response_metadata_disappeared'
        if final:
            self._seen.clear()
        for path in paths:
            try:
                encoded = path.read_bytes()
                digest = hashlib.sha256(encoded).hexdigest()
                if self._seen.get(path.name) == digest:
                    continue
                self.responses.pop(path.stem, None)
                meta = json.loads(encoded)
                operation = meta['native_operation_id']
                if meta['response_state'] == 'recording':
                    if final:
                        self.errors[path.name] = 'incomplete_response_capture_at_native_close'
                    continue
                self._seen[path.name] = digest
                if meta['response_state'] != 'complete' or meta.get('errors'):
                    raise ValueError('incomplete_response_capture:' + str(meta.get('errors')))
                name = meta['response_file']
                if Path(name).name != name:
                    raise ValueError('response_path_not_local')
                raw_path = path.parent / name
                raw = raw_path.read_bytes()
                if len(raw) != meta['captured_bytes'] or hashlib.sha256(raw).hexdigest() != meta['response_sha256']:
                    raise ValueError('response_bytes_hash_or_length_mismatch')
                text = self._decode(raw, meta.get('content_encoding') or 'identity').decode('utf-8')
                pointer = '/scene/id'
                if meta.get('retained_format') == 'redacted_sse_events_json':
                    data = json.loads(text).get('final_response')
                    pointer = '/final_response/scene/id'
                elif 'text/event-stream' in (meta.get('content_type') or ''):
                    documents = []
                    for block in text.replace('\r\n', '\n').split('\n\n'):
                        data = '\n'.join(line[5:].lstrip(' ') for line in block.splitlines() if line.startswith('data:'))
                        if data:
                            document = json.loads(data)
                            if isinstance(document, dict) and document.get('type') == 'done':
                                documents.append(document.get('response'))
                    if len(documents) != 1:
                        raise ValueError('sse_requires_one_complete_native_done_response')
                    data = documents[0]
                    pointer = 'SSE done.response/scene/id'
                else:
                    data = json.loads(text)
                scene = data.get('scene') if isinstance(data, dict) else None
                self.responses[operation] = {
                    'native_operation_id': operation, 'request_id': None,
                    'status': meta['response_status'],
                    'data': {'scene': {'id': scene['id']}} if isinstance(scene, dict) and isinstance(scene.get('id'), str) else {},
                    'server_observation': {'metadata_file': str(path.relative_to(self.root)),
                        'response_file': str(raw_path.relative_to(self.root)), 'response_sha256': meta['response_sha256'],
                        'original_response_sha256': meta.get('original_response_sha256', meta['response_sha256']),
                        'original_response_bytes': meta.get('original_response_bytes', meta['captured_bytes']),
                        'safe_retention': meta.get('safe_retention', 'transient_raw_before_seal'),
                        'scene_pointer': pointer, 'content_encoding': meta.get('content_encoding'),
                        'origin': 'original_native_http_response_bytes'},
                }
                self.errors.pop(path.name, None)
            except (OSError, ValueError, KeyError, TypeError, EOFError, zlib.error, AttributeError, subprocess.SubprocessError) as exc:
                self.responses.pop(path.stem, None)
                self.errors[path.name] = str(exc)
        return self.responses

    def retain_safe_evidence(self):
        """After native shutdown, retain redacted evidence and remove raw secrets.

        Original wire hashes/lengths remain distinct from saved evidence hashes.
        This only changes local recording files, never native HTTP responses.
        """
        from story_benchmark.recording import redact_evidence
        if (self.root / 'manifest.json').exists():
            raise ValueError('sealed_recording_must_not_be_modified')
        transitions = []
        for path in self.directory.glob('*.json'):
            try:
                meta = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                self.errors[path.name] = 'retention_metadata_unreadable:' + type(exc).__name__
                continue
            if meta.get('safe_retention'):
                continue
            name = meta.get('response_file')
            if not isinstance(name, str) or Path(name).name != name:
                self.errors[path.name] = 'unsafe_raw_response_path'
                continue
            raw_path = path.parent / name
            try:
                original = raw_path.read_bytes() if raw_path.is_file() else b''
            except OSError as exc:
                self.errors[path.name] = 'retention_raw_unreadable:' + type(exc).__name__
                continue
            original_hash = hashlib.sha256(original).hexdigest()
            original_verified = (meta.get('response_state') == 'complete' and original_hash == meta.get('response_sha256') and len(original) == meta.get('captured_bytes'))
            try:
                if meta.get('response_state') != 'complete' or original_hash != meta.get('response_sha256') or len(original) != meta.get('captured_bytes'):
                    raise ValueError('incomplete_or_invalid_raw_capture')
                text = self._decode(original, meta.get('content_encoding') or 'identity').decode('utf-8')
                if 'text/event-stream' in (meta.get('content_type') or ''):
                    events = []
                    for block in text.replace('\r\n', '\n').split('\n\n'):
                        value = '\n'.join(line[5:].lstrip(' ') for line in block.splitlines() if line.startswith('data:'))
                        if value:
                            events.append(json.loads(value))
                    final = [e.get('response') for e in events if isinstance(e, dict) and e.get('type') == 'done']
                    document = {'events': events, 'final_response': final[0] if len(final) == 1 else None}
                    retained_format = 'redacted_sse_events_json'
                else:
                    document = json.loads(text)
                    retained_format = 'redacted_json'
                safe = redact_evidence(document)
                metadata_redacted = redact_evidence(meta)
                # Metadata contains hashes, not credentials. Preserve native
                # IDs and field labels, scrub URL-bearing error strings too.
                meta = metadata_redacted
            except (OSError, ValueError, TypeError, EOFError, zlib.error, AttributeError, subprocess.SubprocessError) as exc:
                safe = {'body_unavailable': 'incomplete_or_undecodable_capture', 'exception_type': type(exc).__name__}
                retained_format = 'unavailable_raw_discarded'
                meta['response_state'] = 'capture_failed'
                meta.setdefault('errors', []).append('safe_retention_unavailable:' + type(exc).__name__)
            encoded = (json.dumps(safe, ensure_ascii=False, indent=2) + '\n').encode()
            target = path.parent / (path.stem + '.response.evidence')
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(encoded)
            temporary.replace(target)
            meta.update(original_response_sha256=original_hash if original_verified else None,
                original_response_bytes=len(original) if original_verified else None,
                observed_capture_file_sha256=original_hash, observed_capture_file_bytes=len(original),
                original_content_encoding=meta.get('content_encoding'), original_content_type=meta.get('content_type'),
                response_file=target.name, response_sha256=hashlib.sha256(encoded).hexdigest(), captured_bytes=len(encoded),
                content_encoding='identity', content_type='application/json', retained_format=retained_format,
                safe_retention='redacted_before_seal_original_wire_hash_separate', raw_response_bytes_retained=False)
            temporary_meta = path.with_suffix('.tmp')
            temporary_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            temporary_meta.replace(path)
            if raw_path.is_file():
                raw_path.unlink()
            transitions.append({'metadata_file': str(path.relative_to(self.root)),
                'discarded_raw_file': str(raw_path.relative_to(self.root)), 'retained_file': str(target.relative_to(self.root)),
                'original_sha256': original_hash if original_verified else None, 'retained_sha256': meta['response_sha256'], 'retained_format': retained_format})
        # A failed metadata write must never leave an unindexed sensitive body
        # in a sealed root. These are exclusively this observer's transient files.
        for raw_path in self.directory.glob('*.response.raw'):
            if raw_path.is_file():
                raw = raw_path.read_bytes()
                transitions.append({'discarded_raw_file': str(raw_path.relative_to(self.root)),
                    'partial_capture_sha256': hashlib.sha256(raw).hexdigest(), 'partial_capture_bytes': len(raw),
                    'retained_format': 'unavailable_orphan_raw_discarded'})
                self.errors[raw_path.name] = 'orphan_or_unreadable_raw_removed_before_seal'
                raw_path.unlink()
        self._seen.clear()
        self.responses.clear()
        self.refresh(final=True)
        return transitions

    def audit(self, browser_headers):
        self.refresh(final=True)
        expected = {h['native_operation_id'] for h in browser_headers.values() if h.get('native_operation_id')}
        missing = sorted(expected - set(self.responses))
        return {'status': 'incomplete' if missing or self.errors else 'complete',
            'expected_operations_from_browser_headers': sorted(expected),
            'verified_response_operations': sorted(self.responses), 'missing_operations': missing,
            'capture_errors': self.errors, 'body_source': 'original_local_native_http_response',
            'cdp_body_cache_required_for_lineage': False}

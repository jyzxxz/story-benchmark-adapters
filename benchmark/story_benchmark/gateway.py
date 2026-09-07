"""One-run, observe-only OpenAI transport for text, vision and image roles.

All native attempts, including retries and unused image candidates, are retained.
No adapter retry or resource cap. Provider/native client limits remain intact.
"""
from __future__ import annotations

import base64
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import IncompleteRead
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
import time
from urllib import request, error
from urllib.parse import urlsplit
import uuid

from .io import BenchmarkError, redact, sha256
from .recording import utc_now


def normalize_usage(usage):
    usage = usage if isinstance(usage, dict) else {}
    def number(*keys):
        for key in keys:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                return value
        return None
    return {'version':'provider_usage_v1', 'input_tokens':number('input_tokens','prompt_tokens'),
        'output_tokens':number('output_tokens','completion_tokens'), 'total_tokens':number('total_tokens'),
        'input_details':usage.get('input_tokens_details',usage.get('prompt_tokens_details')),
        'output_details':usage.get('output_tokens_details',usage.get('completion_tokens_details')),
        'details_included_in_totals':'provider_defined_not_added'}


def parse_payload(body, content_type):
    if 'multipart/form-data' not in content_type:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise BenchmarkError('model_payload_not_object')
        return value, None
    message = BytesParser(policy=default).parsebytes(
        ('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
    if not message.is_multipart():
        raise BenchmarkError('invalid_multipart')
    value, parts = {}, []
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b''
        parts.append({'name':name, 'filename':filename, 'data':data,
                      'content_type':part.get_content_type()})
        if filename is None:
            if name in value:
                raise BenchmarkError('duplicate_multipart_field')
            value[name] = data.decode('utf-8')
    return value, parts


def encode_multipart(parts, model):
    boundary = 'benchmark-' + secrets.token_hex(16)
    chunks = []
    for part in parts:
        name = part['name']
        if not isinstance(name, str) or any(c in name for c in ('\r','\n','"')):
            raise BenchmarkError('invalid_multipart_name')
        filename = part['filename']
        disposition = 'Content-Disposition: form-data; name="'+name+'"'
        if filename is not None:
            disposition += '; filename="'+Path(filename).name.replace('"','_')+'"'
        chunks.extend([('--'+boundary+'\r\n'+disposition+'\r\nContent-Type: '+part['content_type']+'\r\n\r\n').encode(),
                       str(model).encode() if name == 'model' and filename is None else part['data'], b'\r\n'])
    chunks.append(('--'+boundary+'--\r\n').encode())
    return b''.join(chunks), 'multipart/form-data; boundary='+boundary


class ModelGateway:
    def __init__(self, recorder, providers, *, request_transform=None):
        self.recorder = recorder
        self.providers = providers
        self.request_transform = request_transform
        self.api_key = secrets.token_urlsafe(32)
        self._condition = threading.Condition()
        self._active = 0
        self._closed = False
        self._outputs = {}
        self._download_threads = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_POST(self):
                gateway._handle(self)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def url(self, role):
        if role not in self.providers:
            raise BenchmarkError('unknown_provider_role')
        return f'http://127.0.0.1:{self.server.server_port}/{role}/v1'

    def output_for_call(self, call_id, index=0):
        with self._condition:
            return dict(self._outputs[(call_id,index)]) if (call_id,index) in self._outputs else None

    def output_for_hash(self, digest, call_id=None, candidate_index=None):
        with self._condition:
            matches=[dict(v) for (call,idx),v in self._outputs.items() if v.get('file_sha256')==digest
                and (call_id is None or call==call_id) and (candidate_index is None or idx==candidate_index)]
        return matches[0] if len(matches)==1 else None

    def close(self):
        with self._condition:
            self._closed = True
        self.server.shutdown()
        self.wait_for_idle()
        self.server.server_close()
        self.thread.join(5)

    def wait_for_idle(self):
        """Drain sent requests and capture downloads; never cancel provider work."""
        with self._condition:
            while self._active:
                self._condition.wait(0.5)
        for thread in self._download_threads:
            thread.join()

    def _reject(self, handler, status, reason):
        payload = json.dumps({'error':{'message':reason,'type':'benchmark_transport'}}).encode()
        try:
            handler.send_response(status); handler.send_header('Content-Type','application/json')
            handler.send_header('Content-Length',str(len(payload))); handler.end_headers();handler.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def _handle(self, handler):
        call_id = uuid.uuid4().hex
        entered = False
        sent = False
        terminal_written = False
        began = time.monotonic_ns()
        started_utc = utc_now()
        role = None
        common = {}
        chunks = []
        headers_sent = False
        disconnected = False
        try:
            if handler.headers.get('Authorization') != 'Bearer '+self.api_key:
                self._reject(handler,401,'run_gateway_authentication_failed');return
            parts = handler.path.strip('/').split('/')
            if len(parts)!=4 or parts[1]!='v1' or parts[0] not in self.providers:
                self._reject(handler,404,'unsupported_model_endpoint');return
            role=parts[0]; endpoint='/'.join(parts[2:])
            if (role in ('text','vision') and endpoint!='chat/completions') or (role=='image' and endpoint not in ('images/generations','images/edits')):
                self._reject(handler,404,'role_endpoint_mismatch');return
            with self._condition:
                if self._closed:
                    self._reject(handler,503,'run_observation_closed');return
                self._active+=1; entered=True
            if not handler.headers.get('Content-Length'):
                raise BenchmarkError('content_length_required')
            body=handler.rfile.read(int(handler.headers['Content-Length']))
            payload, multipart=parse_payload(body,handler.headers.get('Content-Type','application/json'))
            provider=self.providers[role]
            if payload.get('model') != provider['model']:
                raise BenchmarkError('native_model_differs_from_common_'+role)
            before=json.loads(json.dumps(payload))
            if self.request_transform is not None:
                payload=self.request_transform(role,endpoint,payload)
            payload.update(provider.get('parameters',{}))
            if multipart is None:
                wire=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
                content_type='application/json'
                request_evidence={'payload':payload,'before_payload':before,'multipart_files':[]}
            else:
                if payload!=before:
                    raise BenchmarkError('multipart_parameters_must_remain_native')
                wire=body;content_type=handler.headers['Content-Type']
                files=[]
                for idx,part in enumerate(multipart):
                    if part['filename'] is not None:
                        rel=f'telemetry/raw/{call_id}-reference-{idx}.bin'
                        self.recorder.save_bytes(rel,part['data'])
                        files.append({'field':part['name'],'file':rel,'sha256':sha256(part['data']),
                                      'bytes':len(part['data']),'media_type':part['content_type']})
                request_evidence={'payload':payload,'before_payload':before,'multipart_files':files}
            request_file=f'telemetry/raw/{call_id}-request.json'
            self.recorder.save_json(request_file,request_evidence)
            endpoint_url=provider['base_url'].rstrip('/')+'/'+endpoint
            secret=os.environ.get(provider['api_key_env'])
            if not secret:
                raise BenchmarkError('missing_provider_credential:'+role)
            common={'call_id':call_id,'root_run_id':self.recorder.run_id,'role':role,'purpose':'generation',
                'endpoint':endpoint,'request_file':request_file,'requested_model':payload.get('model'),
                'start_utc':started_utc,'start_monotonic_ns':began,'clock_id':self.recorder.clock_id,
                'observer':'model_gateway','request_attempt':True,'usage_raw':None,
                'usage_normalized':normalize_usage(None),'response_file':None,'provider_request_id':None,
                'native_task_id':handler.headers.get('X-Benchmark-Native-Task-Id'),
                'native_stage':handler.headers.get('X-Benchmark-Native-Stage'),
                'native_operation_id':handler.headers.get('X-Benchmark-Native-Operation-Id')}
            self.recorder.event('model_request_started',call_id=call_id,role=role,observer='model_gateway')
            req=request.Request(endpoint_url,data=wire,method='POST',headers={
                'Authorization':'Bearer '+secret,'Content-Type':content_type,
                'Accept':handler.headers.get('Accept','application/json')})
            sent=True
            try:
                upstream=request.urlopen(req,timeout=None)
            except error.HTTPError as exc:
                upstream=exc
            status=upstream.status
            response_type=upstream.headers.get('Content-Type','application/json')
            provider_id=upstream.headers.get('x-request-id') or upstream.headers.get('request-id')
            try:
                handler.send_response(status)
                handler.send_header('Content-Type',response_type)
                handler.send_header('Connection','close')
                handler.send_header('x-benchmark-call-id',call_id)
                for name in ('x-request-id','retry-after'):
                    if upstream.headers.get(name):handler.send_header(name,upstream.headers[name])
                handler.end_headers();headers_sent=True
            except (BrokenPipeError,ConnectionResetError):
                disconnected=True
            handler.close_connection=True
            first=True
            with upstream:
                while True:
                    data=upstream.read1(65536) if hasattr(upstream,'read1') else upstream.read(65536)
                    if not data:
                        if getattr(upstream,'length',None) not in (None,0):
                            raise IncompleteRead(b'',upstream.length)
                        break
                    chunks.append(data)
                    if first:
                        first=False;self.recorder.event('model_first_chunk',call_id=call_id,role=role,observer='model_gateway')
                    # Image callers need candidate identity before native transforms begin.
                    if not disconnected and role!='image':
                        try:handler.wfile.write(data);handler.wfile.flush()
                        except (BrokenPipeError,ConnectionResetError):disconnected=True
            raw=b''.join(chunks)
            objects=[];parse_errors=[]
            if 'text/event-stream' in response_type:
                for line_number,line in enumerate(raw.decode('utf-8','replace').splitlines()):
                    if line.startswith('data:') and line[5:].strip() not in ('','[DONE]'):
                        try:objects.append(json.loads(line[5:]))
                        except ValueError:parse_errors.append(line_number)
                parsed={'stream_events':objects,'body_utf8':raw.decode('utf-8','replace'),'parse_error_lines':parse_errors}
            else:
                try:parsed=json.loads(raw);objects=[parsed]
                except (ValueError,UnicodeError):parsed={'unparsed_response':True,'byte_length':len(raw),'body_utf8':raw.decode('utf-8','replace')}
            response_file=f'telemetry/raw/{call_id}-response.json'
            self.recorder.save_json(response_file,parsed)
            usages=[o.get('usage') for o in objects if isinstance(o,dict) and isinstance(o.get('usage'),dict) and o['usage']]
            usage=usages[-1] if usages else None
            if role=='image':
                data=parsed.get('data') if isinstance(parsed,dict) else None
                returned=len(data) if isinstance(data,list) else 0 if status>=400 else None
                self.recorder.append('images/requests.jsonl',{**common,'status_code':status,
                    'returned_count':returned,'delivery':'completed','response_file':response_file})
                if isinstance(data,list):
                    self._candidates(call_id,data,response_file)
                if not disconnected:
                    try:handler.wfile.write(raw);handler.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError):disconnected=True
            terminal={**common,'end_utc':utc_now(),'end_monotonic_ns':time.monotonic_ns(),
                'delivery':'completed','status_code':status,'native_client_disconnected':disconnected,
                'response_file':response_file,'provider_request_id':provider_id,
                'actual_model':next((o.get('model') for o in reversed(objects) if isinstance(o,dict) and o.get('model')),None),
                'usage_raw':usage,'usage_normalized':normalize_usage(usage),'parse_error_lines':parse_errors,
                'error':None if status<400 else {'http_status':status}}
            self.recorder.append('telemetry/calls.jsonl',terminal);terminal_written=True
            self.recorder.event('model_request_finished',call_id=call_id,role=role,observer='model_gateway',delivery='completed',status_code=status)
        except Exception as exc:
            if isinstance(exc,IncompleteRead) and exc.partial:
                chunks.append(exc.partial)
            code='delivery_unknown' if sent else 'adapter_transport_rejected'
            self.recorder.error(code,str(exc),call_id=call_id,role=role,wire_send_attempted=sent)
            if sent and not terminal_written:
                partial_file=f'telemetry/raw/{call_id}-partial-response.json'
                self.recorder.save_json(partial_file,{'transport_complete':False,'byte_length':sum(map(len,chunks)),
                    'body_utf8':b''.join(chunks).decode('utf-8','replace')})
                self.recorder.append('telemetry/calls.jsonl',{**common,'delivery':'unknown',
                    'end_utc':utc_now(),'end_monotonic_ns':time.monotonic_ns(),
                    'response_file':partial_file,'native_client_disconnected':disconnected,
                    'error':{'type':type(exc).__name__,'message':str(exc)}})
                if role=='image':
                    self.recorder.append('images/requests.jsonl',{**common,'delivery':'unknown','returned_count':None})
            if not headers_sent:self._reject(handler,502 if sent else 400,code)
            else:handler.close_connection=True
        finally:
            if entered:
                with self._condition:
                    self._active-=1;self._condition.notify_all()

    def _candidates(self,call_id,items,response_file):
        for index,item in enumerate(items):
            output_id=call_id+'-candidate-'+str(index)
            candidate={'output_id':output_id,'call_id':call_id,'candidate_index':index,
                'response_file':response_file,'response_pointer':'/data/'+str(index),
                'provider_url':item.get('url') if isinstance(item,dict) else None,
                'file':None,'file_sha256':None,'download_status':'pending',
                'revised_prompt':item.get('revised_prompt') if isinstance(item,dict) else None}
            with self._condition:self._outputs[(call_id,index)]=candidate
            def download(item=item,candidate=candidate,index=index):
                try:
                    if item.get('b64_json'):
                        data=base64.b64decode(item['b64_json'],validate=True)
                    elif item.get('url'):
                        url=item['url'];parsed=urlsplit(url)
                        if parsed.scheme not in ('http','https') or parsed.username or parsed.password:
                            raise BenchmarkError('unsupported_candidate_url')
                        with request.urlopen(url,timeout=120) as response:data=response.read()
                    else:
                        raise BenchmarkError('candidate_has_no_image_payload')
                    suffix='.png' if data.startswith(b'\x89PNG') else '.jpg' if data.startswith(b'\xff\xd8') else '.webp' if data[8:12]==b'WEBP' else '.bin'
                    rel='images/files/'+output_id+suffix
                    self.recorder.save_bytes(rel,data)
                    candidate.update(file=rel,file_sha256=sha256(data),download_status='saved')
                    try:
                        from PIL import Image
                        import io
                        with Image.open(io.BytesIO(data)) as bitmap:
                            rgba=bitmap.convert('RGBA')
                            candidate.update(pixel_sha256=sha256(str(rgba.size).encode()+rgba.tobytes()),
                                             width=rgba.width,height=rgba.height,pixel_hash_rule='rgba_dimensions_bytes_v1')
                    except (ImportError,OSError,ValueError):
                        candidate.update(pixel_sha256=None,pixel_hash_rule=None)
                except Exception as exc:
                    candidate.update(download_status='failed',download_error=str(exc))
                    self.recorder.error('image_candidate_save_failed',str(exc),output_id=candidate['output_id'],call_id=call_id)
                self.recorder.append('images/outputs.jsonl',candidate)
                with self._condition:self._outputs[(call_id,index)]=candidate
            thread=threading.Thread(target=download,daemon=True)
            self._download_threads.append(thread);thread.start()

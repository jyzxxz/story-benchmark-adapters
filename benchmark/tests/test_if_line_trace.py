import json
from types import SimpleNamespace
import httpx
from openai import AsyncOpenAI
import pytest
from native_shims.if_line import trace

@pytest.fixture
def env(monkeypatch, tmp_path):
    for k, v in {'BENCH_RUN_ID':'test-root','BENCH_TRACE_DIR':str(tmp_path),'BENCH_BUDGET_MODE':'bounded','BENCH_MAX_CALLS':'10',
                 'BENCH_MAX_OUTPUT_TOKENS':'100','BENCH_MAX_INPUT_CHARS':'10000'}.items():
        monkeypatch.setenv(k,v)
    monkeypatch.delenv('LLM_MODEL',raising=False)
    monkeypatch.setenv('BENCH_MODEL_PARAMETERS','{}')
    return tmp_path

def events(root):
    return [json.loads(x) for file in root.glob('if_line-*.jsonl') for x in file.read_text().splitlines()]

@pytest.mark.asyncio
async def test_http_json_and_missing_usage(env):
    sent = []
    async def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'id':'r1','model':'actual','choices':[{'message':{'content':'原文'},'finish_reason':'stop'}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    trace.instrument_client_kwargs({'http_client':client})
    payload = {'model':'requested','messages':[{'role':'user','content':'任务'}],'max_tokens':20}
    with trace.task_context(SimpleNamespace(id='task1',project_id=9,kind='bible.generate',attempt=1)):
        response = await client.post('https://example.test/chat/completions',json=payload,headers={'Authorization':'Bearer secret'})
    assert response.json()['choices'][0]['message']['content'] == '原文'
    assert sent == [payload]
    records = events(env)
    assert records[-1]['usage'] is None
    assert records[-1]['actual_model'] == 'actual'
    assert records[-1]['native_task_id'] == 'task1'
    assert 'Bearer secret' not in json.dumps(records)
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_tee_preserves_bytes_and_single_usage(env):
    raw = b'data: {"id":"s1","choices":[{"delta":{"content":"x"},"finish_reason":null}]}\n\ndata: {"choices":[],"usage":{"total_tokens":17}}\n\ndata: [DONE]\n\n'
    class Parts(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield raw[:12]
            yield raw[12:]
    async def respond(request):
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Parts())
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    trace.instrument_client_kwargs({'http_client':client})
    async with client.stream('POST','https://example.test',json={'messages':[], 'max_tokens':20}) as response:
        received = b''.join([chunk async for chunk in response.aiter_bytes()])
    assert received == raw
    records = events(env)
    assert len(records) == 2
    assert records[-1]['usage'] == {'total_tokens':17}
    await client.aclose()


@pytest.mark.asyncio
async def test_sdk_internal_retry_each_http_counted(env):
    count = 0
    async def respond(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429, headers={'retry-after':'0.01'},json={'error':{'message':'retry'}})
        return httpx.Response(200, json={'id':'r2','model':'fake','object':'chat.completion',
            'created':0,'choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':4,'completion_tokens':1,'total_tokens':5}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    client = AsyncOpenAI(api_key='fake-unit-test-key',base_url='https://example.test',
        max_retries=1, **trace.instrument_client_kwargs({'http_client':http}))
    result = await client.chat.completions.create(model='fake',messages=[{'role':'user','content':'test'}],max_tokens=20)
    assert result.choices[0].message.content == 'ok'
    records = events(env)
    assert len([r for r in records if r['event']=='started']) == 2
    assert json.loads((env/'budget.json').read_text())['calls'] == 2
    assert records[-1]['usage']['total_tokens'] == 5
    await client.close()


@pytest.mark.asyncio
async def test_budget_blocks_before_wire(env, monkeypatch):
    calls = []
    monkeypatch.setenv('BENCH_MAX_CALLS','1')
    async def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    trace.instrument_client_kwargs({'http_client':client})
    with pytest.raises(RuntimeError,match='per-call'):
        await client.post('https://example.test',json={'messages':[],'max_tokens':101})
    await client.post('https://example.test',json={'messages':[],'max_tokens':20})
    with pytest.raises(RuntimeError,match='budget exhausted'):
        await client.post('https://example.test',json={'messages':[],'max_tokens':20})
    assert len(calls) == 1
    await client.aclose()


def test_redaction():
    assert trace.redact({'api_key':'hidden','cookie':'hidden','nested':['sk-test-key']}) == {
        'api_key':'[REDACTED]','cookie':'[REDACTED]','nested':['[REDACTED]']}


def test_output_budget_clamps_only_sampling(env):
    message=[{'role':'user','content':'不改写的共同任务'}]
    native={'messages':message,'model':'fake','max_tokens':200}
    effective=trace.apply_output_budget(native,('chat','completions'))
    assert effective['max_tokens']==100
    assert effective['messages'] is message
    assert native['max_tokens']==200
    assert trace.apply_output_budget({'messages':message},('chat','completions'))['max_tokens']==100


def test_unlimited_output_parameters_unchanged_or_absent(env,monkeypatch):
    monkeypatch.setenv('BENCH_BUDGET_MODE','unlimited')
    for native in ({'messages':[]},{'messages':[],'max_tokens':6000},{'messages':[],'max_completion_tokens':12000}):
        assert trace.apply_output_budget(native,('chat','completions')) is native
    with pytest.raises(RuntimeError,match='media'):
        trace.apply_output_budget({},('images','generate'))


@pytest.mark.asyncio
async def test_unlimited_wire_ignores_stale_caps_and_preserves_native_bytes(env,monkeypatch):
    monkeypatch.setenv('BENCH_BUDGET_MODE','unlimited')
    for key in ('BENCH_MAX_CALLS','BENCH_MAX_INPUT_CHARS','BENCH_MAX_OUTPUT_TOKENS'):
        monkeypatch.setenv(key,'1')
    sent=[]
    async def respond(request):
        sent.append(request.content)
        return httpx.Response(200,json={'model':'native-model','choices':[],
            'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}})
    client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    trace.instrument_client_kwargs({'http_client':client})
    payloads=[b'{ "model":"native-model", "messages":[] }',
              json.dumps({'model':'native-model','messages':[{'role':'user','content':'长输入'*10000}],'max_tokens':6000}).encode()]
    for raw in payloads:
        await client.post('https://example.test',content=raw)
    assert sent==payloads
    state=json.loads((env/'budget.json').read_text())
    assert state['calls']==2 and state['reserved_output_tokens'] is None and state['budget_mode']=='unlimited'
    records=events(env)
    assert len([r for r in records if r['event']=='completed'])==2
    assert all(r['budget_output_cap'] is None and r['budget_mode']=='unlimited' for r in records)
    assert records[-1]['usage']['total_tokens']==5
    await client.aclose()


def test_unlimited_worker_receipt_has_null_limits(env,monkeypatch):
    monkeypatch.setenv('BENCH_BUDGET_MODE','unlimited')
    monkeypatch.setenv('LLM_MODEL','fixture')
    monkeypatch.setenv('OPENAI_BASE_URL','http://127.0.0.1:9/v1')
    trace.write_worker_receipt()
    receipt=json.loads((env/'if_line_worker_receipt.json').read_text())
    assert receipt['budget_mode']=='unlimited'
    assert all(receipt[k] is None for k in ('max_calls','max_output_tokens','max_input_chars','timeout_seconds'))
    assert receipt['native_request_timeouts']=='unchanged'


def test_unknown_actual_model_is_null(env):
    request=httpx.Request('POST','https://example.test',json={'messages':[],'model':'requested','max_tokens':20})
    trace.on_request_sync(request)
    response=httpx.Response(200,request=request,json={'choices':[]})
    trace.on_response_sync(response)
    assert events(env)[0]['actual_model'] is None
    assert events(env)[-1]['actual_model'] is None
    assert events(env)[-1]['requested_model']=='requested'


def test_secrets_in_error_strings(env,monkeypatch):
    monkeypatch.setenv('TEST_NONSTANDARD_API_KEY','fixture-private-key-abcdef')
    text=trace.redact('Bearer bearer-value-abcdef Cookie: sid=cookie-secret\nhttps://host.test/?api_key=query-secret&v=1 fixture-private-key-abcdef')
    for value in ('bearer-value-abcdef','cookie-secret','query-secret','fixture-private-key-abcdef'):
        assert value not in text
    assert trace.redact({'usage':{'output_tokens':12,'total_tokens':16}})['usage']['output_tokens']==12


def test_unanswered_http_call_finalizes_as_delivery_unknown(env):
    request=httpx.Request('POST','https://example.test',json={'messages':[],'model':'fake','max_tokens':20})
    trace.on_request_sync(request)
    trace.finalize_pending()
    rows=events(env)
    assert rows[0]['call_id']==rows[-1]['call_id']
    assert rows[-1]['event']=='error'
    assert rows[-1]['error']['code']=='delivery_unknown'
    assert rows[-1]['usage'] is None


@pytest.mark.asyncio
async def test_wire_model_parameters_are_shared_and_observed(env,monkeypatch):
    monkeypatch.setenv('BENCH_MODEL_PARAMETERS','{"thinking":{"type":"disabled"}}')
    native={'model':'fake','messages':[{'role':'user','content':'共同任务：雨夜'}],'max_tokens':20}
    sent=[]
    async def respond(request):
        sent.append(json.loads(request.content))
        assert int(request.headers['Content-Length'])==len(request.content)
        return httpx.Response(200,json={'model':'fake','choices':[]})
    client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    trace.instrument_client_kwargs({'http_client':client})
    await client.post('https://example.test',json=native)
    assert sent[0]=={**native,'thinking':{'type':'disabled'}}
    assert 'thinking' not in native
    assert events(env)[0]['request_messages']==native['messages']
    assert events(env)[0]['request_schema_and_sampling']['thinking']=={'type':'disabled'}
    await client.aclose()


def test_empty_model_parameters_preserve_original_wire_bytes(env):
    raw=b'{ "model":"fake", "messages":[], "max_tokens":20 }'
    request=httpx.Request('POST','https://example.test',content=raw)
    trace.on_request_sync(request)
    assert request.content==raw


@pytest.mark.asyncio
async def test_injected_parameters_count_toward_input_limit(env,monkeypatch):
    native={'model':'fake','messages':[],'max_tokens':20}
    monkeypatch.setenv('BENCH_MAX_INPUT_CHARS',str(len(json.dumps(native,separators=(',',':')))))
    monkeypatch.setenv('BENCH_MODEL_PARAMETERS','{"thinking":{"type":"disabled"}}')
    sent=[]
    async def respond(request):
        sent.append(request)
        return httpx.Response(200,json={})
    client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    trace.instrument_client_kwargs({'http_client':client})
    with pytest.raises(RuntimeError,match='input character budget'):
        await client.post('https://example.test',json=native)
    assert sent==[]
    assert events(env)[0]['wire_sent'] is False
    await client.aclose()


@pytest.mark.asyncio
async def test_transport_error_is_unknown_and_native_sdk_retry_survives(env):
    calls=[]
    async def respond(request):
        calls.append(request)
        if len(calls)==1: raise httpx.ReadError('fixture transport failed after sending')
        return httpx.Response(200,json={'id':'ok','model':'fake','object':'chat.completion','created':0,
            'choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}]})
    http=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    client=AsyncOpenAI(api_key='fixture-key',base_url='https://example.test',max_retries=1,
        **trace.instrument_client_kwargs({'http_client':http}))
    response=await client.chat.completions.create(model='fake',messages=[],max_tokens=20)
    assert response.choices[0].message.content=='ok'
    records=events(env)
    failed=next(r for r in records if r['event']=='error')
    assert failed['error']['type']=='ReadError'
    assert failed['delivery_status']=='delivery_unknown'
    assert failed['wire_sent'] is None and failed['usage'] is None and failed['actual_model'] is None
    assert len([r for r in records if r['event']=='started'])==2
    trace.finalize_pending()
    assert len(events(env))==len(records)
    await client.close()


def test_sync_transport_error_is_recorded(env):
    def respond(request): raise httpx.ConnectError('fixture connect failure')
    client=httpx.Client(transport=httpx.MockTransport(respond))
    trace.instrument_client_kwargs({'http_client':client},sync=True)
    with pytest.raises(httpx.ConnectError):
        client.post('https://example.test',json={'messages':[],'max_tokens':20})
    assert events(env)[-1]['error']['type']=='ConnectError'
    assert events(env)[-1]['delivery_status']=='delivery_unknown'
    client.close()


def test_constructed_async_client_across_loops_has_no_keepalive_retry(env,monkeypatch):
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    monkeypatch.setenv('BENCH_MODEL_PARAMETERS','{"thinking":{"type":"disabled"}}')
    received=[]
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            raw=json.dumps({'id':'local','model':'fake','object':'chat.completion','created':0,
                'choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=AsyncOpenAI(api_key='local-fixture',base_url=f'http://127.0.0.1:{server.server_port}',
        **trace.instrument_client_kwargs({}))
    async def call():
        response=await client.chat.completions.create(model='fake',messages=[{'role':'user','content':'原始正文任务'}],max_tokens=20)
        assert response.choices[0].message.content=='ok'
    try:
        for _ in range(3): asyncio.run(call())
        asyncio.run(client.close())
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    assert len(received)==3
    assert all(r['thinking']=={'type':'disabled'} for r in received)
    assert len([r for r in events(env) if r['event']=='started'])==3
    assert len([r for r in events(env) if r['event']=='completed'])==3
    assert not [r for r in events(env) if r['event']=='error']

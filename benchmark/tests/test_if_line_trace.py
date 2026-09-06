import json
from types import SimpleNamespace
import httpx
from openai import AsyncOpenAI
import pytest
from native_shims.if_line import trace

@pytest.fixture
def env(monkeypatch, tmp_path):
    for k, v in {'BENCH_RUN_ID':'test-root','BENCH_TRACE_DIR':str(tmp_path),'BENCH_MAX_CALLS':'10',
                 'BENCH_MAX_OUTPUT_TOKENS':'100','BENCH_MAX_INPUT_CHARS':'10000'}.items():
        monkeypatch.setenv(k,v)
    monkeypatch.delenv('LLM_MODEL',raising=False)
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

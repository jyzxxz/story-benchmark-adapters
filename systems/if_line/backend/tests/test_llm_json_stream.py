import json
from types import SimpleNamespace

import pytest

from app.integrations.llm.json_stream import JsonStringFieldStream
from app.integrations.llm.legacy_adapter import LegacyLLMAdapter
from app.services.llm_service import LLMOutputTruncatedError, llm_service


def test_stream_extracts_content_across_chunks_and_decodes_escapes():
    parser = JsonStringFieldStream("content")
    chunks = [
        '{"chapter_index":1,"con',
        'tent":"第一段\\n第',
        '二段：\\u4f60\\u597d\\\"！","ending_hook":"x"}',
    ]
    assert "".join(parser.feed(chunk) for chunk in chunks) == '第一段\n第二段：你好"！'
    assert parser.finished is True


def test_stream_ignores_other_string_fields_named_differently():
    parser = JsonStringFieldStream("content")
    assert parser.feed('{"title":"content: fake",') == ""
    assert parser.feed('"content":"real text"}') == "real text"


def test_stream_rejects_invalid_escape():
    parser = JsonStringFieldStream("content")
    with pytest.raises(ValueError):
        parser.feed('{"content":"bad\\x"}')


class _FakeStream:
    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event


class _FakeStreamingCompletions:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self.events)


def _stream_event(content: str, finish_reason=None):
    return SimpleNamespace(
        id="req-stream",
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
    )


@pytest.mark.asyncio
async def test_chapter_stream_uses_non_thinking_max_budget(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    raw = json.dumps({"content": "完整正文"}, ensure_ascii=False)
    completions = _FakeStreamingCompletions([
        _stream_event(raw),
        _stream_event("", finish_reason="stop"),
    ])
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(llm_service, "client", fake_client)
    deltas = []

    result = await LegacyLLMAdapter().generate_chapter_streaming(
        story_bible={},
        chapter_outline={"chapter_index": 1},
        previous_chapters=[],
        word_count_min=1,
        word_count_max=10,
        uuid=123,
        on_text_delta=deltas.append,
    )

    assert result.data.content == "完整正文"
    assert "".join(deltas) == "完整正文"
    assert completions.calls[0]["max_tokens"] == 32000
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_chapter_stream_reports_length_truncation(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    completions = _FakeStreamingCompletions([
        _stream_event('{"content":"未完成'),
        _stream_event("", finish_reason="length"),
    ])
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(llm_service, "client", fake_client)

    with pytest.raises(LLMOutputTruncatedError, match="max_tokens=32000"):
        await LegacyLLMAdapter().generate_chapter_streaming(
            story_bible={},
            chapter_outline={"chapter_index": 1},
            previous_chapters=[],
            word_count_min=1,
            word_count_max=10,
            uuid=123,
            on_text_delta=lambda _: None,
        )

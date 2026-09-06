import json
import os
from types import SimpleNamespace

import pytest

for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(key, None)

from app.schemas import ChapterGenerateRequest
from app.services.llm_service import LLMService


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.responses.pop(0)
        finish_reason = "stop"
        if isinstance(payload, SimpleNamespace):
            content = payload.content
            finish_reason = payload.finish_reason
        else:
            content = json.dumps(payload, ensure_ascii=False)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                    finish_reason=finish_reason,
                )
            ]
        )


class FakeClient:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def test_chapter_generate_request_defaults_are_expanded():
    request = ChapterGenerateRequest()
    assert request.word_count_min == 3000
    assert request.word_count_max == 4500


@pytest.mark.asyncio
async def test_short_chapter_triggers_one_expansion():
    client = FakeClient([
        {"content": "短" * 100},
        {"content": "长" * 3200},
    ])
    service = LLMService()
    service.client = client

    result = await service.generate_chapter_content(
        story_bible={"worldview": "测试世界"},
        chapter_outline={
            "chapter_index": 1,
            "title": "测试章节",
            "summary": "测试摘要",
            "scene": "测试场景",
            "emotion": "紧张",
        },
        previous_chapters=[],
        word_count_min=3000,
        word_count_max=4500,
        uuid=123,
    )

    assert result.content == "长" * 3200
    assert len(client.completions.calls) == 2
    assert client.completions.calls[0]["max_tokens"] == 16000
    assert client.completions.calls[0]["timeout"] == 180.0
    assert client.completions.calls[1]["max_tokens"] == 16000
    assert client.completions.calls[1]["timeout"] == 180.0


@pytest.mark.asyncio
async def test_long_enough_chapter_does_not_expand():
    client = FakeClient([
        {"content": "达" * 3100},
    ])
    service = LLMService()
    service.client = client

    result = await service.generate_chapter_content(
        story_bible={"worldview": "测试世界"},
        chapter_outline={
            "chapter_index": 2,
            "title": "达标章节",
            "summary": "测试摘要",
            "scene": "测试场景",
            "emotion": "平静",
        },
        previous_chapters=[],
        word_count_min=3000,
        word_count_max=4500,
        uuid=123,
    )

    assert result.content == "达" * 3100
    assert len(client.completions.calls) == 1


@pytest.mark.asyncio
async def test_truncated_chapter_retries_once_with_larger_budget(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    client = FakeClient([
        SimpleNamespace(
            content='{"content":"未完成',
            finish_reason="length",
        ),
        {"content": "完" * 3200},
    ])
    service = LLMService()
    service.client = client

    result = await service.generate_chapter_content(
        story_bible={"worldview": "测试世界"},
        chapter_outline={
            "chapter_index": 3,
            "title": "截断重试",
            "summary": "测试摘要",
            "scene": "测试场景",
            "emotion": "紧张",
        },
        previous_chapters=[],
        word_count_min=3000,
        word_count_max=4500,
        uuid=123,
    )

    assert result.content == "完" * 3200
    assert [call["max_tokens"] for call in client.completions.calls] == [16000, 32000]
    assert all(
        call["extra_body"] == {"thinking": {"type": "disabled"}}
        for call in client.completions.calls
    )


@pytest.mark.asyncio
async def test_repeated_truncation_raises_explicit_error(monkeypatch):
    from app.services.llm_service import LLMOutputTruncatedError

    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    client = FakeClient([
        SimpleNamespace(content='{"content":"第一次', finish_reason="length"),
        SimpleNamespace(content='{"content":"第二次', finish_reason="length"),
    ])
    service = LLMService()
    service.client = client

    with pytest.raises(LLMOutputTruncatedError, match="max_tokens=32000"):
        await service.generate_chapter_content(
            story_bible={"worldview": "测试世界"},
            chapter_outline={
                "chapter_index": 4,
                "title": "重复截断",
                "summary": "测试摘要",
                "scene": "测试场景",
                "emotion": "紧张",
            },
            previous_chapters=[],
            uuid=123,
        )

    assert [call["max_tokens"] for call in client.completions.calls] == [16000, 32000]

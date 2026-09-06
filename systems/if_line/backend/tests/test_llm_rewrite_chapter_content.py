import json
import os
from types import SimpleNamespace

import pytest

for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(key, None)

from app.services.llm_service import LLMService


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(self.response, ensure_ascii=False)
                    )
                )
            ]
        )


class FakeClient:
    def __init__(self, response):
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.mark.asyncio
async def test_rewrite_chapter_content_uses_feedback_prompt_and_returns_schema():
    client = FakeClient({"content": "修订后的正文"})
    service = LLMService()
    service.client = client

    result = await service.rewrite_chapter_content(
        story_bible={"style_rules": "克制、具体"},
        chapter_outline={
            "chapter_index": 3,
            "title": "雨夜重逢",
            "scene": "旧车站",
            "emotion": "戒备转为信任",
        },
        previous_chapters=[{"chapter_index": 2, "summary": "林舟找到车站线索。"}],
        draft_result={"content": "林舟不禁百感交集，时间仿佛静止。"},
        quality_reasons=["正文长度不足"],
        flavor_issues=["套话:不禁"],
        rewrite_hint="Use concrete action instead of abstract emotion.",
        word_count_min=2000,
        word_count_max=3000,
        uuid=123,
    )

    assert result.chapter_index == 3
    assert result.title == "雨夜重逢"
    assert result.content == "修订后的正文"
    assert result.main_scene == "旧车站"
    assert result.emotion == "戒备转为信任"

    assert len(client.completions.calls) == 1
    call = client.completions.calls[0]
    assert call["temperature"] == 0.7
    assert call["max_tokens"] == 8000
    assert call["timeout"] == 180.0
    assert call["response_format"] == {"type": "json_object"}
    assert "正文长度不足" in call["messages"][1]["content"]
    assert "套话:不禁" in call["messages"][1]["content"]
    assert "Use concrete action instead of abstract emotion." in call["messages"][1]["content"]


@pytest.mark.asyncio
async def test_rewrite_chapter_content_normalizes_word_count_range():
    client = FakeClient(
        {
            "chapter_index": 1,
            "title": "测试章节",
            "content": "修订正文",
        }
    )
    service = LLMService()
    service.client = client

    await service.rewrite_chapter_content(
        story_bible={},
        chapter_outline={"chapter_index": 1, "title": "测试章节"},
        previous_chapters=[],
        draft_result={"content": "原稿"},
        quality_reasons=[],
        flavor_issues=[],
        rewrite_hint="",
        word_count_min=3000,
        word_count_max=2000,
    )

    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert "3000 到 3000 个中文字符" in prompt

import json
from types import SimpleNamespace

import pytest

from app.agent import auto_creator
from app.agent.ai_flavor_check import AiFlavorReport
from app.models import ChapterContent, ChapterOutline, Project, StoryBible
from app.schemas import LLMChapterContentOutput


class FakeQuery:
    def __init__(self, *, first=None, all_items=None):
        self._first = first
        self._all = all_items or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class FakeSession:
    def __init__(self, project, bible, outline):
        self.project = project
        self.bible = bible
        self.outline = outline
        self.added = []

    def query(self, model):
        if model is Project:
            return FakeQuery(first=self.project)
        if model is StoryBible:
            return FakeQuery(first=self.bible)
        if model is ChapterOutline:
            return FakeQuery(first=self.outline)
        if model is ChapterContent:
            return FakeQuery(all_items=[])
        raise AssertionError(f"unexpected query model: {model}")

    def add(self, value):
        self.added.append(value)

    def commit(self):
        pass


@pytest.mark.asyncio
async def test_auto_creator_rewrites_failed_flavor_with_feedback(monkeypatch, tmp_path):
    project = SimpleNamespace(status="chapter_generating")
    bible = SimpleNamespace(raw_json={"style_rules": "克制、具体"}, style_rules="克制、具体")
    outline = SimpleNamespace(
        chapter_index=1,
        title="雨夜",
        summary="小明在车站等待",
        conflict="末班车即将离开",
        characters=["小明"],
        scene="车站",
        emotion="焦虑",
    )
    session = FakeSession(project, bible, outline)
    creator = auto_creator.AutoCreator.__new__(auto_creator.AutoCreator)
    creator.db = session
    creator.engine = SimpleNamespace(transition_to=lambda *args: None)
    creator.run_dir = tmp_path
    creator.word_count_min = 100
    creator.word_count_max = 500

    first = LLMChapterContentOutput(
        chapter_index=1,
        title="雨夜",
        content="小明" + "旧" * 350,
    )
    rewritten = LLMChapterContentOutput(
        chapter_index=1,
        title="雨夜",
        content="小明" + "新" * 350,
    )
    rewrite_calls = []
    flavor_reports = iter(
        [
            AiFlavorReport(
                passed=False,
                score=55,
                issues=["模板句:他知道"],
                rewrite_hint="Replace the phrase '他知道' with concrete action.",
            ),
            AiFlavorReport(passed=True, score=86),
        ]
    )

    async def fake_generate(**kwargs):
        return first

    async def fake_rewrite(**kwargs):
        rewrite_calls.append(kwargs)
        return rewritten

    async def fake_flavor_check(text, style_rules):
        return next(flavor_reports)

    monkeypatch.setattr(auto_creator.resilient_llm, "generate_chapter_content", fake_generate)
    monkeypatch.setattr(auto_creator.resilient_llm, "rewrite_chapter_content", fake_rewrite)
    monkeypatch.setattr(auto_creator.ai_flavor_check, "check", fake_flavor_check)

    count = await creator._step_generate_chapters(
        project_id=7,
        chapters=[{"chapter_index": 1}],
    )

    assert count == 1
    assert len(rewrite_calls) == 1
    assert rewrite_calls[0]["flavor_issues"] == ["模板句:他知道"]
    assert "concrete action" in rewrite_calls[0]["rewrite_hint"]
    saved_content = next(item for item in session.added if isinstance(item, ChapterContent))
    assert saved_content.content == rewritten.content

    report = json.loads((tmp_path / "chapter_1_flavor.json").read_text(encoding="utf-8"))
    assert report["attempt"] == 2
    assert report["quality"]["passed"] is True
    assert report["flavor"]["passed"] is True
    assert report["flavor"]["score"] == 86

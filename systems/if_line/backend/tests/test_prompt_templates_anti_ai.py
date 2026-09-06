"""P4.3: 验证 prompt_templates.py 的反 AI 味负例清单。"""
from app.services.prompt_templates import ANTI_AI_NEGATIVE_LIST, get_story_bible_prompt


def test_anti_ai_list_not_empty():
    assert len(ANTI_AI_NEGATIVE_LIST) > 0


def test_anti_ai_list_contains_known_cliches():
    assert "不禁" in ANTI_AI_NEGATIVE_LIST
    assert "仿佛" in ANTI_AI_NEGATIVE_LIST


def test_story_bible_prompt_requires_character_gender():
    prompt = get_story_bible_prompt(
        title="测试",
        characters=["高志远"],
        story_start="开篇",
        story_end="结局",
        style="现实主义",
        pace="medium",
        extra_requirements="",
    )
    assert '"gender"' in prompt
    assert "male/female/unknown" in prompt


def test_story_bible_prompt_accepts_authoring_character_objects():
    prompt = get_story_bible_prompt(
        title="测试",
        characters=[
            {"name": "米拉", "role": "主角"},
            {"canonical_name": "陈屹"},
        ],
        story_start="开篇",
        story_end="结局",
        style="科幻",
        pace="fast",
        extra_requirements="",
    )

    assert "核心角色：米拉, 陈屹" in prompt

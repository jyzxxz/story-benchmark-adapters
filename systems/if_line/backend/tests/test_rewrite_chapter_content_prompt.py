from app.services.prompt_templates import get_rewrite_chapter_content_prompt


def test_rewrite_prompt_contains_draft_feedback_and_constraints():
    prompt = get_rewrite_chapter_content_prompt(
        story_bible={"style_rules": "克制、具体"},
        chapter_outline={
            "chapter_index": 3,
            "title": "雨夜重逢",
            "scene": "旧车站",
            "emotion": "戒备转为信任",
        },
        previous_chapters=[
            {"chapter_index": 1, "summary": "两人在事故后失散。"},
            {"chapter_index": 2, "summary": "林舟查到旧车站的线索。"},
        ],
        draft_result={
            "chapter_index": 3,
            "title": "雨夜重逢",
            "content": "林舟不禁百感交集，时间仿佛静止。",
            "ending_hook": "陌生来电响起",
        },
        quality_reasons=["正文长度不足"],
        flavor_issues=["套话:不禁", "模板句:时间仿佛静止"],
        rewrite_hint="Rewrite the emotion through concrete action and dialogue.",
        word_count_min=2000,
        word_count_max=3000,
    )

    assert "正文长度不足" in prompt
    assert "套话:不禁" in prompt
    assert "Rewrite the emotion through concrete action and dialogue." in prompt
    assert "林舟不禁百感交集，时间仿佛静止。" in prompt
    assert "不要另写一个不同故事" in prompt
    assert '"chapter_index": 3' in prompt
    assert '"title": "雨夜重逢"' in prompt
    assert "2000 到 3000 个中文字符" in prompt


def test_rewrite_prompt_handles_empty_feedback_and_first_chapter():
    prompt = get_rewrite_chapter_content_prompt(
        story_bible={},
        chapter_outline={"chapter_index": 1, "title": '初见“故人”'},
        previous_chapters=[],
        draft_result={"content": "原始正文"},
        quality_reasons=[],
        flavor_issues=[],
        rewrite_hint="",
        word_count_min=1000,
        word_count_max=1500,
    )

    assert "（本章为第一章，无前情可参考）" in prompt
    assert "无额外改写提示" in prompt
    assert '"title": "初见“故人”"' in prompt


def test_rewrite_prompt_preserves_dialogue_density():
    prompt = get_rewrite_chapter_content_prompt(
        story_bible={},
        chapter_outline={"chapter_index": 1, "title": "开篇"},
        previous_chapters=[],
        draft_result={"content": "原始正文"},
        quality_reasons=[],
        flavor_issues=[],
        rewrite_hint="",
        word_count_min=2000,
        word_count_max=3000,
    )
    assert "修订不得降低对白密度" in prompt
    assert "每 500 字至少 3 句直接引语对白" in prompt

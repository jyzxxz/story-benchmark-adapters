"""续写上下文组装:上一章结尾锚 + 大纲梗概弧线层。"""
from app.services.prompt_templates import get_chapter_content_prompt


def _prev(summary: str, index: int = 1) -> dict:
    return {"chapter_index": index, "display_index": index, "summary": summary}


def test_first_chapter_degrades_gracefully():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 1, "title": "开篇"},
        [],
        3000,
        4500,
    )
    assert "（本章为第一章，无前情可参考）" in prompt
    assert "上一章结尾原文" not in prompt


def test_content_prompt_targets_vn_performance():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 1, "title": "开篇"},
        [],
        3000,
        4500,
    )
    assert "视觉小说（VN）创作演出文本" in prompt


def test_content_prompt_enforces_dialogue_density():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 1, "title": "开篇"},
        [],
        3000,
        4500,
    )
    assert "对白密度（硬指标）" in prompt
    assert "每 500 字至少 3 句直接引语对白" in prompt
    assert "主角在本章必须有台词" in prompt
    assert "严禁连续 3 段以上无对白的纯叙述" in prompt


def test_content_prompt_enforces_entrance_description():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 1, "title": "开篇"},
        [],
        3000,
        4500,
    )
    assert "登场描写" in prompt
    assert "登场动作描写" in prompt


def test_ending_anchor_included_verbatim():
    ending = "河面在月光下泛着银白色的光。" * 40
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 2, "title": "承章"},
        [_prev(ending)],
        3000,
        4500,
        ancestor_outline_summaries=[
            {"display_index": 1, "title": "废墟", "summary": "第一章大纲梗概"}
        ],
    )
    assert "【上一章结尾原文（本章必须从此处自然衔接）】" in prompt
    assert ending in prompt
    assert "衔接铁律" in prompt
    assert "严禁重写、复述" in prompt


def test_arc_layer_uses_outline_summaries():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 3, "title": "转章"},
        [_prev("第一章结尾" + "。" * 1200, 1), _prev("第二章结尾" + "。" * 1200, 2)],
        3000,
        4500,
        ancestor_outline_summaries=[
            {"display_index": 1, "title": "废墟", "summary": "梗概一"},
            {"display_index": 2, "title": "天才", "summary": "梗概二"},
        ],
    )
    assert "梗概一" in prompt and "梗概二" in prompt


def test_arc_layer_falls_back_to_prev_summaries():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 2, "title": "承章"},
        [_prev("兜底前情摘要")],
        3000,
        4500,
    )
    assert "兜底前情摘要" in prompt
    assert "【上一章结尾原文（本章必须从此处自然衔接）】" in prompt


def test_branch_choice_pseudo_chapter_uses_fork_header():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 2, "title": "分叉后首章"},
        [
            _prev("第一章结尾" + "。" * 1200, 1),
            {"kind": "branch_choice", "summary": "选择了黑暗路线"},
        ],
        3000,
        4500,
    )
    assert "【分叉衔接参照（本章必须从此处自然衔接）】" in prompt
    assert "选择了黑暗路线" in prompt
    assert "【上一章结尾原文" not in prompt


def test_last_chapter_requires_full_ending():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 1, "title": "终章"},
        [],
        3000,
        4500,
        total_chapters=1,
    )
    assert "收尾铁律" in prompt
    assert "严禁留悬念" in prompt


def test_non_last_chapter_keeps_hook():
    prompt = get_chapter_content_prompt(
        {"worldview": "w"},
        {"chapter_index": 2, "title": "承章"},
        [_prev("上一章结尾。")],
        3000,
        4500,
        total_chapters=8,
    )
    assert "收尾铁律" not in prompt
    assert "保持悬念钩子" in prompt

# E07 — background-test-split-by-visual-state

## 背景

新链路核心改进之一：**同 location 但日夜/天气/状态变化 → 切成 2 scene**。需要专项测试验证这个能力——给定章节正文（同地点但状态变化），analyzer 必须输出 2 个 scene，且 split_reason 明确。

## SOTA 实践

**pytest fixtures + golden data**（[`docs.pytest.org/en/stable/fixtures.html`](https://docs.pytest.org/en/stable/fixtures.html)）：测试数据与断言分离，便于维护。

**OpenAI Evals assertion-based grading**（[`github.com/openai/evals`](https://github.com/openai/evals)）：每 case 含 input + expected_assertion。

**Property-based testing (Hypothesis)**（[`hypothesis.readthedocs.io/`](https://hypothesis.readthedocs.io/)）：随机生成测试 case 覆盖更多边界。

## 落地建议

`backend/tests/test_background_scene_split.py`：

```python
import pytest
import json
from pathlib import Path

SPLIT_CASES = [
    {
        "case_id": "SPLIT01",
        "case_name": "同地点日夜变化",
        "genre": "historical",
        "chapter_content": """
        午后阳光直射太守府庭院，林夜与苏晚晴穿过月亮门...
        (中略 200 字)
        入夜后，太守府中庭只剩几盏残灯，雨水顺着飞檐流下...
        """,
        "outline": {"scene": "太守府", "characters": ["林夜", "苏晚晴"]},
        "expectations": {
            "scene_count": 2,
            "expected_split_reasons": ["opening", "time_change"],
            "expected_time_buckets": ["day", "night"],
        }
    },
    {
        "case_id": "SPLIT02",
        "case_name": "同地点天气变化",
        "genre": "modern",
        "chapter_content": """
        清晨的码头雾气弥漫，货轮正在卸货...
        (中略)
        下午时分，码头上空乌云密布，暴风雨骤然降临...
        """,
        "outline": {"scene": "码头", "characters": ["沈朗"]},
        "expectations": {
            "scene_count": 2,
            "expected_split_reasons": ["opening", "weather_change"],
        }
    },
    {
        "case_id": "SPLIT03",
        "case_name": "同地点物理状态变化",
        "genre": "scifi",
        "chapter_content": """
        轨道空间站走廊明亮整洁，宇航员往来穿梭...
        (中略)
        爆炸发生后，走廊一片狼藉，金属碎片散落，应急灯闪烁...
        """,
        "outline": {"scene": "空间站走廊", "characters": ["凯尔"]},
        "expectations": {
            "scene_count": 2,
            "expected_split_reasons": ["opening", "physical_state_change"],
        }
    },
    {
        "case_id": "SPLIT04",
        "case_name": "同地点人群状态变化",
        "genre": "historical",
        "chapter_content": """
        集市正值高峰，人声鼎沸，摊贩叫卖...
        (中略)
        夜深后，集市空无一人，只剩散落的箩筐...
        """,
        "outline": {"scene": "集市", "characters": ["阿夜"]},
        "expectations": {
            "scene_count": 2,
            "expected_split_reasons": ["opening", "crowd_state_change"],
        }
    },
    {
        "case_id": "SPLIT05",
        "case_name": "单纯对话推进，可视环境未变",
        "genre": "historical",
        "chapter_content": """
        林夜坐在书房内，听着苏晚晴分析案情...
        (中略 200 字对话)
        苏晚晴说完最后一句话，林夜陷入沉思...
        """,
        "outline": {"scene": "书房", "characters": ["林夜", "苏晚晴"]},
        "expectations": {
            "scene_count": 1,  # 不应切
            "expected_split_reasons": ["opening"],
        }
    },
]

@pytest.mark.parametrize("case", SPLIT_CASES)
@pytest.mark.asyncio
async def test_scene_split_by_visual_state(case, analyzer):
    specs = await analyzer.analyze_chapter(
        chapter_index=0,
        chapter_content=case["chapter_content"],
        outline=ChapterOutline(**case["outline"]),
        story_bible=StoryBible(),
        genre=case["genre"],
    )

    # scene count
    assert len(specs) == case["expectations"]["scene_count"], \
        f"expected {case['expectations']['scene_count']} scenes, got {len(specs)}"

    # split_reasons 顺序匹配
    actual_reasons = [s.split_reason for s in specs]
    expected_reasons = case["expectations"]["expected_split_reasons"]
    assert actual_reasons == expected_reasons, \
        f"split reasons mismatch: expected {expected_reasons}, got {actual_reasons}"

    # 各 scene 的 environment_description 不能含剧情词
    for spec in specs:
        for kw in ["发现", "到达", "调查", "赴约"]:
            assert kw not in spec.environment_description
```

CI 流程：`pytest tests/test_background_scene_split.py -v`。

## 风险与权衡

1. **LLM 非确定性 → SPLIT04 偶发切 3 scene**：权衡：每 case 跑 3 次取多数；允许 ±1 容忍。
2. **case_id 维护**：5 个 case 太少覆盖不全。权衡：首版 5 case 抓核心场景；后续根据 prod 日志的 split_reason 分布扩展。

## 完成判据自检

- ✅ 围绕"切场景测试"，不跑题到 no-plot 测试（E08）。
- ✅ 引用 pytest fixtures、OpenAI Evals、Hypothesis。
- ✅ 落到 `backend/tests/test_background_scene_split.py`。
- ✅ 符合哲学：测试守护切场景逻辑，不引入新模型供应商。

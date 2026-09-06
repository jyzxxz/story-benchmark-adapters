# A12 — background-scene-analyzer-regression-test-set

## 背景

analyzer 是 LLM 驱动的，输出有非确定性。没有 golden set 就无法：
- 评估 system prompt 调整是否带来质量提升
- 评估 model upgrade（glm-4-flash → glm-4.6）的稳定性提升
- 在重构后回归测试（防止 prompt 改坏）

需要 10 章固定"黄金章节正文"，覆盖单场景 / 双场景 / 多场景 / 同地点多状态 / 公共活动 / 室内私有 / 自然户外 / 异空间 等代表性 case。

## SOTA 实践

**OpenAI Evals framework**（[`github.com/openai/evals`](https://github.com/openai/evals)）：

> 每个 test case = (input, expected_fact, grading_method)。grading 可以是 exact_match / fuzzy_match / model_graded。

**promptfoo**（[`github.com/promptfoo/promptfoo`](https://github.com/promptfoo/promptfoo)）：

> YAML-based test assertions；支持 `contains`、`not-contains`、`regex`、`icontains` 等多种断言。

**pytest fixtures + parametrize**（[`docs.pytest.org/en/stable/parametrize.html`](https://docs.pytest.org/en/stable/parametrize.html)）：把 golden set 组织成参数化 fixture，每个 case 一个独立 test function。

## 落地建议

目录：`backend/tests/fixtures/background_analyzer_golden/`

每个 case 一个 JSON 文件：

```json
{
  "case_id": "GA01",
  "case_name": "古风单场景_野战营帐",
  "genre": "historical",
  "chapter_content": "...(200-400 字)...",
  "outline": { "scene": "...", "characters": ["林夜", "苏晚晴"] },
  "story_bible": {
    "characters": [{"name": "林夜", "english_name": "Lin Ye", "alias": ["阿夜"]}]
  },
  "expectations": {
    "scene_count": 1,
    "expected_scene_types": ["war_camp"],
    "expected_people_policy_mode": "empty_required",
    "must_contain_keywords_in_description": ["营帐", "旗帜"],
    "must_not_contain_keywords_in_description": ["林夜", "苏晚晴", "发现", "到达"],
    "expected_split_reasons": ["opening"]
  }
}
```

10 个 case 覆盖：

| ID | 类型 | 期望 scene 数 |
|---|---|---|
| GA01 | 古风单场景（野战营帐） | 1 |
| GA02 | 古风双场景（白天集市→夜里密室） | 2 |
| GA03 | 古风多场景（朝会→御花园→寝宫） | 3 |
| GA04 | 古风同地点多状态（大厅整洁→大厅战后狼藉） | 2 |
| GA05 | 现代公共活动（地铁早高峰） | 1 (groups) |
| GA06 | 现代私人室内（深夜办公室） | 1 (empty) |
| GA07 | 科幻异空间（轨道空间站走廊） | 1 |
| GA08 | 奇幻梦境（梦境之海） | 1 |
| GA09 | 动漫日常（学校天台雨后黄昏） | 1 |
| GA10 | 混合 genre 跨场景（4 scene） | 4 |

落到 `backend/tests/test_background_analyzer_golden.py`：

```python
@pytest.mark.parametrize("case_file", sorted(glob.glob("fixtures/background_analyzer_golden/*.json")))
@pytest.mark.asyncio
async def test_golden_case(case_file, analyzer):
    case = json.load(open(case_file))
    specs = await analyzer.analyze_chapter(
        chapter_index=case["case_id"],
        chapter_content=case["chapter_content"],
        outline=ChapterOutline(**case["outline"]),
        story_bible=StoryBible(**case["story_bible"]),
        genre=case["genre"],
    )
    assert len(specs) == case["expectations"]["scene_count"]
    for spec, exp_type, exp_policy in zip(specs, ...):
        assert spec.scene_type in case["expectations"]["expected_scene_types"]
        assert spec.people_policy.mode == exp_policy
        for kw in case["expectations"]["must_contain_keywords_in_description"]:
            assert kw in spec.environment_description
        for kw in case["expectations"]["must_not_contain_keywords_in_description"]:
            assert kw not in spec.environment_description
```

CI 流程：`pytest tests/test_background_analyzer_golden.py -v`。

## 风险与权衡

1. **LLM 非确定性 → golden set 偶发失败**：同一 case 跑两次可能 scene_count 不一致。权衡：每个 case 跑 3 次，要求 2/3 通过；或在 assertion 上加 ±1 容忍度。
2. **golden set 维护成本**：system prompt 调整后可能需要重写 expectations。权衡：用 `pytest --update-golden` flag 自动重生成 expectations，加人工 review。

## 完成判据自检

- ✅ 围绕"回归测试 golden set"，不跑题到具体 analyzer 实现。
- ✅ 引用 OpenAI Evals、promptfoo、pytest parametrize。
- ✅ 落到 `backend/tests/fixtures/background_analyzer_golden/` 和 `test_background_analyzer_golden.py`。
- ✅ 符合哲学：用测试守护重构稳定性，不引入新供应商。

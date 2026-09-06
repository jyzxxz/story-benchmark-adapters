# B10 — background-style-regression-test-set

## 背景

classifier 是 LLM 驱动且依赖 system prompt + 词表，每次调整都可能引入 regression。需要 30 个固定 `BackgroundSceneSpec` 作为 golden set，每个有期望 tags，断言：
- 输出 3-5 个 tag
- 覆盖 ≥3 维度
- 每个 tag 在 taxonomy 词表中
- 不含人物/动作/对白词

## SOTA 实践

**OpenAI Evals**（[`github.com/openai/evals`](https://github.com/openai/evals)）：每 case 一行 input + 期望 + grading method。

**promptfoo assertions**（[`www.promptfoo.dev/docs/configuration/expected-outputs/`](https://www.promptfoo.dev/docs/configuration/expected-outputs/)）：支持 `contains-all`、`is-json`、`schema-validate` 等多种断言。

**pytest parametrize**（[`docs.pytest.org/en/stable/parametrize.html`](https://docs.pytest.org/en/stable/parametrize.html)）：YAML/JSON 驱动的测试用例组织。

## 落地建议

目录：`backend/tests/fixtures/background_style_golden/`

每个 case JSON：

```json
{
  "case_id": "GB01",
  "case_name": "古风战场夜景",
  "genre": "historical",
  "spec": {
    "scene_id": "test_s1",
    "scene_name": "战场夜营",
    "scene_type": "war_camp",
    "environment_description": "夜幕降临的军营，火把映照着帐篷...",
    "time_of_day": "night",
    "weather": "clear",
    "atmosphere": "萧索",
    "people_policy": {"mode": "empty_required"},
    "forbidden_characters": ["林夜"]
  },
  "expectations": {
    "tag_count": [3, 5],
    "must_cover_dimensions": ["art_style", "mood"],
    "must_contain_any": {
      "art_style": ["写实国风电影感", "古风厚涂"],
      "mood": ["萧索", "压迫"]
    },
    "must_not_contain": ["少女", "战斗紧张", "对峙"]
  }
}
```

30 个 case 覆盖：5 genre × 6 场景类型（室内私有 / 室内公共 / 城市户外 / 自然户外 / 军事户外 / 特殊）。

测试：

```python
@pytest.mark.parametrize("case_file", sorted(glob.glob("fixtures/background_style_golden/*.json")))
@pytest.mark.asyncio
async def test_style_classify_golden(case_file, classifier):
    case = json.load(open(case_file))
    spec = BackgroundSceneSpec(**case["spec"])
    tags = await classifier.classify(spec, case["genre"])

    # 1. tag 数量
    min_n, max_n = case["expectations"]["tag_count"]
    assert min_n <= len(tags) <= max_n

    # 2. 覆盖维度
    dimensions = {t.dimension for t in tags}
    for d in case["expectations"]["must_cover_dimensions"]:
        assert d in dimensions

    # 3. 词表内
    for t in tags:
        assert classifier._in_taxonomy(t, case["genre"])

    # 4. 必含词
    for dim, allowed in case["expectations"]["must_contain_any"].items():
        actual = [t.value for t in tags if t.dimension == dim]
        assert any(v in actual for v in allowed), f"none of {allowed} in {actual}"

    # 5. 禁含词
    for forbidden in case["expectations"]["must_not_contain"]:
        for t in tags:
            assert forbidden not in t.value
```

## 风险与权衡

1. **LLM 非确定性 → 30 case 偶发 fail**：同 A12，每个 case 跑 3 次取多数。
2. **golden set 维护**：taxonomy 升级后 expectations 失效。权衡：`pytest --update-golden` 自动重生，加人工 review。

## 完成判据自检

- ✅ 围绕"golden set 测试组织"，不跑题到具体 LLM 调用。
- ✅ 引用 OpenAI Evals、promptfoo、pytest parametrize。
- ✅ 落到 `backend/tests/fixtures/background_style_golden/` 和 `test_background_style_golden.py`。
- ✅ 符合哲学：测试守护重构稳定性，不引入新供应商。

# C10 — background-assembler-regression-test-set

## 背景

assembler 是 deterministic 的，输出可严格断言——给定相同输入必须产出完全相同输出。这让 golden set 测试比 LLM 路径更可靠。

30 个 `(scene_spec, style_tags) → expected_prompt` 的 golden set，每个断言：
- 必含关键 phrase（environment_description 全文、style tag value）
- 不含剧情词（中文动词 / 角色名非 exclusion 段）
- 不含角色名（除 Named cast exclusion 段）

## SOTA 实践

**pytest snapshot testing**（[`github.com/tophat/syrupy`](https://github.com/tophat/syrupy)）：deterministic 输出用 snapshot 比对，diff 清晰。

**OpenAI golden set eval**（[`github.com/openai/evals`](https://github.com/openai/evals)）：input → expected 的标准化组织。

**JSON Schema output assertion**（[`json-schema.org/understanding-json-schema/reference/combining`](https://json-schema.org/understanding-json-schema/reference/combining)）：用 schema 校验输出结构。

## 落地建议

目录：`backend/tests/fixtures/background_assembler_golden/`

每个 case JSON：

```json
{
  "case_id": "GC01",
  "case_name": "古风野战营帐夜景",
  "input": {
    "spec": {
      "scene_id": "test_s1",
      "scene_name": "夜营",
      "scene_selector": "war_camp__night__clear__empty",
      "scene_type": "war_camp",
      "environment_description": "夜幕降临的军营...",
      "time_of_day": "night",
      "weather": "clear",
      "atmosphere": "萧索",
      "camera_shot_type": "high_angle_far",
      "people_policy": {"mode": "empty_required"},
      "forbidden_characters": ["林夜", "苏晚晴"]
    },
    "style_tags": [
      {"dimension": "art_style", "value": "写实国风电影感"},
      {"dimension": "color_palette", "value": "暮色紫灰"},
      {"dimension": "mood", "value": "萧索"}
    ]
  },
  "expectations": {
    "must_contain": ["夜幕降临的军营", "写实国风电影感", "Strictly empty environment"],
    "must_not_contain_anywhere": ["发现", "到达", "赴约", "战斗紧张"],
    "must_not_contain_except_named_cast_section": ["林夜", "苏晚晴"],
    "named_cast_section_must_contain": ["林夜", "苏晚晴"]
  }
}
```

测试：

```python
@pytest.mark.parametrize("case_file", sorted(glob.glob("fixtures/background_assembler_golden/*.json")))
def test_assembler_golden(case_file, assembler):
    case = json.load(open(case_file))
    spec = BackgroundSceneSpec(**case["input"]["spec"])
    tags = [StyleTag(**t) for t in case["input"]["style_tags"]]

    prompt = assembler.assemble(spec, tags, forbidden_characters=spec.forbidden_characters)
    enforced = image_gen._enforce_background_environment_focus(spec, prompt, spec.forbidden_characters)

    # 1. 必含
    for kw in case["expectations"]["must_contain"]:
        assert kw in enforced, f"missing '{kw}'"

    # 2. 任何位置都不能有剧情词
    for kw in case["expectations"]["must_not_contain_anywhere"]:
        assert kw not in enforced, f"forbidden plot word '{kw}' found"

    # 3. 角色名只能在 Named cast exclusion 段
    for char in case["expectations"]["must_not_contain_except_named_cast_section"]:
        # 找出 Named cast exclusion 段
        nc_match = re.search(r"Named cast exclusion.*?(?:\n\n|$)", enforced, re.DOTALL)
        nc_section = nc_match.group(0) if nc_match else ""
        rest = enforced.replace(nc_section, "")
        assert char not in rest, f"char name '{char}' leaked outside Named cast section"

    # 4. Named cast 段必须含所有角色名
    for char in case["expectations"]["named_cast_section_must_contain"]:
        assert char in nc_section, f"Named cast section missing '{char}'"
```

30 case 覆盖：5 genre × 6 scene_category，每 case 用不同 people_policy.mode（empty_required / optional / required 各 10 个）。

## 风险与权衡

1. **deterministic 测试很严格，assembler 改一行就 fail**：这是 feature，不是 bug——重构应当谨慎。权衡：snapshot diff 让 reviewer 容易看出改动是否合理。
2. **30 case 维护成本**：每加一个新功能（如新 ban phrase）就要更新 expectations。权衡：用 `pytest --update-golden` 自动重生 + 人工 review。

## 完成判据自检

- ✅ 围绕"assembler golden set"，不跑题到 enforce 或 rewriter。
- ✅ 引用 syrupy snapshot、OpenAI Evals、JSON Schema assertion。
- ✅ 落到 `backend/tests/fixtures/background_assembler_golden/` 和 `test_background_assembler_golden.py`。
- ✅ 符合哲学：deterministic 测试守护，不引入新模型供应商。

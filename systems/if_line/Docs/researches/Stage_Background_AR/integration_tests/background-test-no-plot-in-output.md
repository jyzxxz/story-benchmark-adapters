# E08 — background-test-no-plot-in-output

## 背景

新链路最关键的承诺：**所有背景 prompt 不含剧情动词和角色名**（除 named cast exclusion 段）。每次跑 golden set 都必须断言这点，防 regression。

剧情黑名单覆盖：发现 / 到达 / 看见 / 听见 / 调查 / 赴约 / 杀 / 死 / 攻击 / 等。

## SOTA 实践

**SD community "prompt regex"**（[`github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features#negative-prompt`](https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features#negative-prompt)）：用 regex 检查 prompt 是否含禁词，工业级 quality gate。

**Content moderation blacklists**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：关键词列表 + regex pattern 双重过滤。

**Pytest parametrize + assertion**（[`docs.pytest.org`](https://docs.pytest.org)）：把每个 golden case 都跑同一组断言。

## 落地建议

`backend/tests/test_background_no_plot.py`：

```python
PLOT_VERBS_ZH = [
    "发现", "到达", "看见", "听见", "意识到", "思考",
    "调查", "赴约", "交易", "对决", "对峙",
    "杀", "死", "攻击", "受伤", "倒下",
    "走向", "拔剑", "举枪", "扣动扳机",
    "说", "问", "回答", "呐喊",
]
PLOT_VERBS_EN = [
    "discovers", "arrives", "sees", "investigates", "approaches",
    "kills", "dies", "attacks", "wounds", "falls",
    "said", "asked", "shouted", "confronted",
]

def _check_no_plot(prompt: str, allowed_named_chars: list[str] = None):
    """断言 prompt 不含剧情动词；角色名只能在 Named cast exclusion 段"""
    failures = []

    # 1. 任何位置都不能含剧情动词
    for kw in PLOT_VERBS_ZH + PLOT_VERBS_EN:
        if kw in prompt.lower():
            failures.append(f"plot verb '{kw}' found in prompt")

    # 2. 角色名只能在 Named cast exclusion 段
    nc_match = re.search(r"Named cast exclusion.*?(?:\n\n|\Z)", prompt, re.DOTALL)
    nc_section = nc_match.group(0) if nc_match else ""
    rest_of_prompt = prompt.replace(nc_section, "")
    for char in (allowed_named_chars or []):
        if char in rest_of_prompt:
            failures.append(f"char name '{char}' leaked outside Named cast section")

    return failures


@pytest.mark.parametrize("case", GOLDEN_CASES_FULL_PIPELINE)
@pytest.mark.asyncio
async def test_no_plot_in_background_prompt(case, full_pipeline):
    """跑完整 analyzer → classifier → assembler → enforce 链路，断言 no plot"""
    specs = await full_pipeline.analyzer.analyze_chapter(...)
    style_map = await full_pipeline.classifier.classify_many(specs, case["genre"])

    for spec in specs:
        spec.style_tags = style_map.get(spec.scene_id, [])
        prompt = full_pipeline.assembler.assemble(spec, spec.style_tags)
        prompt = full_pipeline.image_gen._enforce_background_environment_focus(
            spec, prompt, spec.forbidden_characters
        )

        failures = _check_no_plot(prompt, allowed_named_chars=spec.forbidden_characters)
        assert not failures, f"scene {spec.scene_id}: {failures}"
```

**额外断言**（更严格）：

```python
def test_enforce_includes_named_cast_section():
    """所有 enforce 后的 prompt 必须有 Named cast exclusion 段"""
    for spec in GOLDEN_SPECS:
        prompt = build_and_enforce(spec)
        assert "Named cast exclusion" in prompt

def test_cn_plot_strip_fallback():
    """如果故意注入中文剧情，strip 函数应清除（D08）"""
    bad_prompt = "[Environment] bedroom\n苏墨赴约，宋雪以情报交易为名..."
    stripped = image_gen._strip_chinese_segments(bad_prompt)
    assert "赴约" not in stripped
    assert "交易" not in stripped
    assert "bedroom" in stripped  # 英文段保留
```

## 风险与权衡

1. **黑名单覆盖不全**：例如 "对话" / "讨论" 没在词表。权衡：定期根据 prod 日志扩展词表；CI fail 时增加新词。
2. **误伤合法英文 tag**：例如 "investigation atmosphere" 是合法 mood tag，但 "investigation" 含 "investigat" substring 与 PLOT_VERBS_EN 部分匹配。权衡：用 word boundary `\binvestigat` 或完整单词匹配。

## 完成判据自检

- ✅ 围绕"no plot 断言"，不跑题到 forbidden_chars 测试（E09）。
- ✅ 引用 SD prompt regex、OpenAI moderation blacklists、pytest parametrize。
- ✅ 落到 `backend/tests/test_background_no_plot.py`。
- ✅ 符合哲学：测试守护反剧情污染底线，不引入新模型供应商。

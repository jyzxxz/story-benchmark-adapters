# B04 — background-style-coverage-rule

## 背景

如果只输出 1 个 style_tag（例如只 `art_style=赛博朋克`），下游 assembler 拼出的 prompt 在 color_palette / lens / texture / mood 维度都是空——CogView-4 会按默认（通常是中性灰）出图，丢失场景氛围。

必须强制 classifier 输出覆盖 ≥3 维度的 tag 列表。这是**质量保证的硬规则**，不能依赖 LLM 自觉。

## SOTA 实践

**Pydantic v2 validator**（[`docs.pydantic.dev/latest/concepts/validators/`](https://docs.pydantic.dev/latest/concepts/validators/)）：用 `@model_validator(mode="after")` 在 schema 层做跨字段约束。

**JSON Schema `minItems` / `maxContains`**（[`json-schema.org/understanding-json-schema/reference/array`](https://json-schema.org/understanding-json-schema/reference/array)）：标准化的"集合基数约束"，OpenAI Structured Outputs 原生支持。

**pytest parametrize + assertion**（[`docs.pytest.org`](https://docs.pytest.org)）：golden set 测试时把"覆盖 ≥3 维度"作为强断言，CI 中自动 fail。

## 落地建议

两层保护：

**1. Pydantic validator（运行时硬约束）**：

```python
class StyleTagList(BaseModel):
    tags: list[StyleTag] = Field(min_length=3, max_length=5)

    @model_validator(mode="after")
    def check_coverage(self):
        dimensions = {t.dimension for t in self.tags}
        if len(dimensions) < 3:
            raise ValueError(
                f"style_tags must cover >=3 dimensions, got {len(dimensions)}: {dimensions}"
            )
        return self
```

**2. service 层校验**（更明确报错）：

```python
def _validate_coverage(self, tags: list[StyleTag]):
    dimensions = {t.dimension for t in tags}
    if len(dimensions) < 3:
        raise StyleCoverageError(
            f"only {len(dimensions)} dimensions covered: {dimensions}"
        )
    if len(tags) > 5:
        raise StyleCoverageError(f"too many tags: {len(tags)} > 5")
    # 每维度最多 1 个 tag（避免 art_style 选了 3 个）
    counts = Counter(t.dimension for t in tags)
    over = [d for d, c in counts.items() if c > 1]
    if over:
        raise StyleCoverageError(f"duplicate dimensions: {over}")
```

校验失败 → `_deterministic_fallback(spec, genre)`。

**3. golden set assertion**（B10）：

```python
def test_golden_classify_case(case, classifier):
    tags = await classifier.classify(case.spec, case.genre)
    dimensions = {t.dimension for t in tags}
    assert len(dimensions) >= 3
    assert 3 <= len(tags) <= 5
```

## 风险与权衡

1. **强制 ≥3 维度让 LLM 凑数**：例如没合适的 mood tag，硬选一个不匹配的。权衡：词表设计时保证每 genre 每 dimension 至少有 3-5 个可选词，避免"无词可选"。
2. **3 维度下限太松**：理想是 5 维度全选。权衡：3 维度是最低底线，assembler 在 prompt 拼接时若发现某维度缺失，可用 spec.atmosphere / lighting 字段补足。

## 完成判据自检

- ✅ 围绕"≥3 维度覆盖规则"，不跑题到 LLM 调用或 system prompt。
- ✅ 引用 Pydantic v2 validator、JSON Schema 数组约束、pytest。
- ✅ 落到 `schemas.py::StyleTagList` 和 `background_style_classifier_service.py::_validate_coverage`。
- ✅ 符合哲学：用 schema validator 保证质量底线，不引入新模型供应商。

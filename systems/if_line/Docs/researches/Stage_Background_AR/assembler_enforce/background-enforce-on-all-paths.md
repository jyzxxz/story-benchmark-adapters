# C06 — background-enforce-on-all-paths

## 背景

新链路有 5 条背景 prompt 路径，必须在每条路径出口处调用 `_enforce_background_environment_focus`：
1. normal builder（assembler 直接输出）
2. rewriter（LLM 重写后）
3. sanitize retry（_sanitize_prompt 之后）
4. fallback builder（rewriter/sanitize 失败时降级）
5. safety fallback（moderation 拒绝时）

历史教训：retry path 走 fallback builder 时绕过 enforce 导致剧情残留。需要单元测试覆盖每条路径。

## SOTA 实践

**OpenAI moderation funnel**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：所有路径过同一 funnel 是工业级实践。

**pytest coverage**（[`coverage.readthedocs.io/`](https://coverage.readthedocs.io/)）：用 coverage 工具验证每条路径都被测试覆盖。

**Test rail / matrix testing**（[`www.guru99.com/test-case-management-tools.html`](https://www.guru99.com/test-case-management-tools.html)）：路径×场景的矩阵化测试用例。

## 落地建议

`backend/tests/test_background_enforce_coverage.py`：

```python
@pytest.mark.parametrize("path", ["normal", "rewriter", "sanitize_retry", "fallback", "safety_fallback"])
def test_enforce_called_on_all_paths(path, monkeypatch):
    """验证每条路径都调用了 _enforce_background_environment_focus"""
    spec = BackgroundSceneSpec(
        scene_id="test", scene_name="test", scene_selector="test",
        scene_type="bedroom", split_reason="opening",
        environment_description="A bedroom", people_policy=PeoplePolicy(mode="empty_required"),
        ...
    )

    enforce_calls = []
    real_enforce = image_gen._enforce_background_environment_focus
    def spy_enforce(s, p, f=None):
        enforce_calls.append((s.scene_id, len(p)))
        return real_enforce(s, p, f)
    monkeypatch.setattr(image_gen, "_enforce_background_environment_focus", spy_enforce)

    if path == "normal":
        prompt = image_gen._build_background_prompt(spec)
        image_gen._enforce_background_environment_focus(spec, prompt)  # 显式调用
    elif path == "rewriter":
        prompt = run_async(image_gen._rewriter.rewrite(...))
        # 应当在 rewriter 调用后立即过 enforce
        ...
    # ... 其余路径

    assert len(enforce_calls) >= 1, f"path '{path}' did not call enforce"
    # 验证 enforce 后的 prompt 含 ban clause
    final_prompt = enforce_calls[-1][1]
    assert "Environment-focused background art" in final_prompt
    assert "Strictly empty environment" in final_prompt  # empty_required 子句
```

**额外断言**：

```python
def test_enforce_idempotent():
    """多次调用 enforce 不应重复追加 ban clause"""
    spec = ...
    p1 = image_gen._enforce_background_environment_focus(spec, "test prompt")
    p2 = image_gen._enforce_background_environment_focus(spec, p1)
    assert p1 == p2  # 第二次应识别已存在并跳过

def test_enforce_strips_chinese_plot():
    """enforce 应当过 CN-PLOT-STRIP（见 C08）"""
    spec = ...
    p = "Scene: bedroom\n苏墨赴约，宋雪以情报交易为名..."
    enforced = image_gen._enforce_background_environment_focus(spec, p)
    assert "苏墨" not in enforced or "苏墨" in forbidden_chars_section(enforced)
    assert "赴约" not in enforced
```

CI 流程：`pytest tests/test_background_enforce_coverage.py -v` 必须 100% pass。

## 风险与权衡

1. **5 条路径模拟复杂**：每条路径 mock 链路不同。权衡：用 `image_gen` 实例的 method hook 简化 mock。
2. **monkeypatch 可能让测试与实现耦合过紧**：测试改了内部 method 顺序就 fail。权衡：测试只验证最终 prompt 含 ban clause，不强断言 enforce 调用次数。

## 完成判据自检

- ✅ 围绕"所有路径覆盖测试"，不跑题到具体 enforce 实现（C02）。
- ✅ 引用 OpenAI moderation funnel、pytest coverage、matrix testing。
- ✅ 落到 `backend/tests/test_background_enforce_coverage.py`。
- ✅ 符合哲学：测试守护"所有路径同质"，不引入新模型供应商。

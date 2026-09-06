# E10 — background-test-validator-retry

## 背景

validator + retry 流程（D01-D07）是质量保证的关键。需要测试：
1. validator 第 1 次判 fail → 自动 retry
2. 第 2 次 retry 强化 ban clause
3. 第 3 次降级 people_policy 到 empty_required
4. 最终通过 / 失败状态正确

mock validator 让第 1 次返回 needs_retry=True，第 2 次返回 passed=True。

## SOTA 实践

**Mock objects for stateful flows**（[`docs.python.org/3/library/unittest.mock.html`](https://docs.python.org/3/library/unittest.mock.html)）：用 side_effect 模拟多次调用不同返回值。

**Stateful testing in pytest**（[`docs.pytest.org/en/stable/`](https://docs.pytest.org/en/stable/)）：用 fixture 管理测试状态。

**OpenAI Evals "trajectory grading"**（[`github.com/openai/evals`](https://github.com/openai/evals)）：评估多步流程的中间状态，不只看最终结果。

## 落地建议

`backend/tests/test_background_validator_retry.py`：

```python
@pytest.mark.asyncio
async def test_validator_retry_succeeds_on_second_attempt(monkeypatch):
    """第 1 次 fail，第 2 次通过"""
    spec = make_test_spec(people_policy="empty_required")

    # Mock validator：第 1 次 fail，第 2 次 pass
    call_count = {"n": 0}
    real_validate = image_gen._validator.validate
    async def mock_validate(image_bytes, spec, prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ValidationResult(passed=False, stage="bbox_hard_fail",
                                     reason="single_prominent_person", is_hard_fail=True)
        return ValidationResult(passed=True, stage="bbox_hard_pass")
    monkeypatch.setattr(image_gen._validator, "validate", mock_validate)

    # Mock image gen（避免真调 CogView-4）
    async def mock_gen(prompt, **kwargs):
        return b"fake_image_bytes"
    monkeypatch.setattr(image_gen, "_generate_image", mock_gen)

    result = await image_gen.generate_background_with_validation(spec, "test prompt")

    assert result.status == "success"
    assert result.retry_count == 1
    assert call_count["n"] == 2  # validator 调了 2 次


@pytest.mark.asyncio
async def test_validator_retry_escalates_policy_after_2_fails(monkeypatch):
    """连续 2 次 fail → 第 3 次 people_policy 降级"""
    spec = make_test_spec(people_policy="background_groups_required")

    call_count = {"n": 0}
    seen_modes = []
    async def mock_validate(image_bytes, spec, prompt):
        call_count["n"] += 1
        seen_modes.append(spec.people_policy.mode)
        if call_count["n"] <= 2:
            return ValidationResult(passed=False, reason="single_prominent_person",
                                     stage="bbox_hard_fail", is_hard_fail=True)
        return ValidationResult(passed=True, stage="bbox_hard_pass")
    monkeypatch.setattr(image_gen._validator, "validate", mock_validate)

    async def mock_gen(prompt, **kwargs):
        return b"fake"
    monkeypatch.setattr(image_gen, "_generate_image", mock_gen)

    result = await image_gen.generate_background_with_validation(spec, "test prompt")

    assert result.status == "success"
    assert seen_modes == ["background_groups_required", "background_groups_required", "empty_required"]
    # 前 2 次原 mode，第 3 次降级


@pytest.mark.asyncio
async def test_validator_max_retries_exceeded(monkeypatch):
    """retry 3 次都 fail → 最终 status='failed'"""
    spec = make_test_spec(people_policy="empty_required")

    async def mock_validate(image_bytes, spec, prompt):
        return ValidationResult(passed=False, reason="single_prominent_person",
                                 stage="bbox_hard_fail", is_hard_fail=True)
    monkeypatch.setattr(image_gen._validator, "validate", mock_validate)

    async def mock_gen(prompt, **kwargs):
        return b"fake"
    monkeypatch.setattr(image_gen, "_generate_image", mock_gen)

    result = await image_gen.generate_background_with_validation(spec, "test prompt")

    assert result.status == "failed"
    assert result.retry_count == 3


@pytest.mark.asyncio
async def test_hard_fail_not_saved_to_asset(monkeypatch, db_session):
    """硬失败的图不应保存到 Asset 表"""
    spec = make_test_spec(people_policy="empty_required")

    async def mock_validate(image_bytes, spec, prompt):
        return ValidationResult(passed=False, reason="single_prominent_person",
                                 stage="bbox_hard_fail", is_hard_fail=True)
    monkeypatch.setattr(image_gen._validator, "validate", mock_validate)

    await asset_svc._persist_background_asset_if_passed(spec, gen_result_fail, db_session)

    assets = await db_session.query(Asset).all()
    assert len(assets) == 0  # 不应有 asset


def make_test_spec(people_policy: str) -> BackgroundSceneSpec:
    return BackgroundSceneSpec(
        scene_id="test", scene_name="test", scene_selector="test__empty",
        scene_type="bedroom", split_reason="opening",
        environment_description="A bedroom with moonlight",
        time_of_day="night", weather="clear", atmosphere="calm",
        lighting="dim", camera_shot_type="interior_wide",
        people_policy=PeoplePolicy(mode=people_policy),
        forbidden_characters=["林夜"],
    )
```

## 风险与权衡

1. **mock 不够真实**：mock validate 永远 fail，真实场景更复杂。权衡：保留 mock 测核心逻辑；额外加 e2e 测试（E12）测真路径。
2. **测试与实现耦合**：mock 改内部 method 顺序就 fail。权衡：测试只验证最终 status 和 retry_count，不强断言内部 method。

## 完成判据自检

- ✅ 围绕"validator retry 测试"，不跑题到 e2e（E12）。
- ✅ 引用 unittest.mock、pytest fixtures、OpenAI Evals trajectory grading。
- ✅ 落到 `backend/tests/test_background_validator_retry.py`。
- ✅ 符合哲学：测试守护 retry 流程，不引入新模型供应商。

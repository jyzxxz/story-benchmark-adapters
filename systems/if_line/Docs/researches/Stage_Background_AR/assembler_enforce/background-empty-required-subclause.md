# C04 — background-empty-required-subclause

## 背景

`empty_required` 是 `people_policy.mode` 三档中最严格的——任何人物都不允许。这是默认 mode（私有室内 / 自然户外 / 异空间）。需要专项设计子句措辞，确保 CogView-4 真的"画空场"。

控制变量测试：相同 scene spec、相同 seed，仅 people_policy.mode 不同，对比生成图——empty_required 应该完全无人。

## SOTA 实践

**CogView-4 ablation 测试**（[`github.com/THUDM/CogView4`](https://github.com/THUDM/CogView4) README）：在 same seed + same prompt 主体下，仅修改 negative prompt 子句，对比生成图——这是评估 phrase 效果的标准方法。

**SD "isolation grid" tool**（[`github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features#x-y-plot`](https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features#x-y-plot)）：用 X-Y plot 在相同 prompt 下扫不同 negative，可视化对比。

## 落地建议

`empty_required` 子句内容（在 C03 已配置）：

```
Strictly empty environment: no people, no humans, no silhouettes, no pedestrians, no guards, no soldiers, no servants, no crowd. Empty environment only.
```

**对比测试方法**：

```python
# tests/test_empty_required_ablation.py
@pytest.mark.parametrize("scene_spec,seed", EMPTY_REQUIRED_GOLDEN)
async def test_empty_required_no_persons(scene_spec, seed, image_gen, validator):
    # 仅 empty_required mode
    scene_spec.people_policy.mode = "empty_required"
    prompt = assembler.assemble(scene_spec, style_tags=[...])
    image = await image_gen.generate(prompt, seed=seed)

    # validator bbox 检测：必须 0 人物框
    result = validator.validate(image, scene_spec)
    assert result.person_count == 0, f"empty_required scene has {result.person_count} persons"
```

10 个 golden 场景：bedroom / study / forest / riverbank / cave / dreamscape / ruins / corridor / temple_interior / private_room。

**每个 case 跑 3 seed**（seed=42, 100, 999），3 seed 全 0 人物才算通过——避免单 seed 偶发。

## 风险与权衡

1. **empty_required 措辞过严 → CogView-4 出错图**：例如生成纯色块。权衡：保留 base clause 的"environment-focused"段作为正向锚定，让模型知道"画空场而不是画空"。
2. **不同 scene_type 对 empty 反应不同**：bedroom 容易画空（无人房间），但 banquet_hall 强制 empty 会"违和"。权衡：empty_required 只在 scene_type ∈ {private, natural, abandoned, dreamlike} 时启用，public 场景永远不允许 empty_required。

## 完成判据自检

- ✅ 围绕"empty_required 子句 + ablation 测试"，不跑题到其他 mode。
- ✅ 引用 CogView-4 ablation、SD X-Y plot。
- ✅ 落到 `image_generation_profiles.json::background_negative_clauses.empty_required` 和 `tests/test_empty_required_ablation.py`。
- ✅ 符合哲学：精确措辞 + 测试守护，不引入新模型供应商。

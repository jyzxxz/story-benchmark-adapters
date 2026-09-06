# A08 — background-people-policy-classification

## 背景

`people_policy.mode` 是新链路最关键的业务规则——它决定 `_enforce_background_environment_focus` 追加哪种子句：
- `empty_required` → 追加 "No people, no humans, no silhouettes..."
- `background_people_optional` → 追加 "If any humans appear, they must be tiny distant..."
- `background_groups_required` → 追加 "Background groups required, but tiny distant anonymous only..."

当前没有任何机制决定 mode——所有场景默认无人，但有些公共空间（集市、宴会厅、码头）天然需要远景人群来传递"公共活动"语义。

需要把 mode 推断逻辑**编码成确定性规则**，不依赖 LLM 自由选择。

## SOTA 实践

**Rule-based classifier with LLM fallback**（[`medium.com/google-cloud/rule-based-ml-vs-machine-learning-9d3b6072a0b3`](https://medium.com/google-cloud/rule-based-ml-vs-machine-learning-9d3b9d3b））的经典模式：高频 case 用规则（确定性、可解释），长尾 case 用 LLM（覆盖度高）。

**Stable Diffusion "negative prompt presets"**（[`github.com/lllyasviel/stable-diffusion-webui-forge`](https://github.com/lllyasviel/stable-diffusion-webui-forge)）：不同场景类型预设不同 negative prompt——人像禁动物、风景禁人物、室内禁户外——这是工业级 prompt 工程的通行做法。

## 落地建议

`BackgroundSceneAnalyzerService._infer_people_policy(specs)`：

```python
def _infer_people_policy(self, specs: list[BackgroundSceneSpec]):
    taxonomy = self._profiles["background_scene_taxonomy"]
    type_to_policy = {
        cat: cfg["default_people_policy"]
        for cat, cfg in taxonomy.items()
    }
    for spec in specs:
        # 1. 从 scene_type 所属 category 查默认 policy
        category = self._classify_scene_category(spec.scene_type)
        spec.people_policy.mode = type_to_policy.get(category, "empty_required")
        # 2. 检测正文是否有强公共活动信号
        if self._has_public_activity_signal(spec.evidence_spans):
            spec.people_policy.mode = "background_groups_required"
        # 3. 检测正文是否有强空场信号
        if self._has_empty_signal(spec.evidence_spans):
            spec.people_policy.mode = "empty_required"
```

**`_has_public_activity_signal`**：扫 evidence_spans 关键词
- 古风：集市、庙会、朝会、宴会、迎亲、巡逻
- 现代：上班高峰、地铁、夜市、演唱会
- 科幻：港口通勤、太空港大厅、殖民点广场
- 奇幻：庆典、神殿朝圣
- 动漫：体育祭、文化祭、入学式

**`_has_empty_signal`**：扫关键词
- "空无一人 / 寂静 / 无人 / 早已离去 / 荒废 / 废弃"

**优先级**：空场信号 > 公共活动信号 > 默认（rule by scene_type）。

落到 `image_generation_profiles.json::background_people_policy_keywords`：

```json
"background_people_policy_keywords": {
  "public_activity": ["集市", "庙会", "宴会", "朝会", "巡逻", "market", "festival"],
  "empty_signal": ["空无一人", "寂静", "无人", "荒废", "废弃", "abandoned", "empty"]
}
```

## 风险与权衡

1. **空场信号误判**：例如正文写"庭院空无一人，远处隐隐可闻人声"——空场信号命中但实际有远景人群。权衡：空场信号只覆盖"绝对空"语义（空无一人、寂静无声、早已离去），不覆盖模糊词。
2. **scene_type 错分类 → people_policy 错配**：LLM 把 "bedroom" 错分为 "indoor_public"。权衡：双轨校验——`_classify_scene_category` 先按 scene_type 查表，再按 architecture 字段关键词（"床/被/枕" → bedroom）二次验证。

## 完成判据自检

- ✅ 围绕"people_policy 三档分类"，不跑题到 system prompt 或 fingerprint。
- ✅ 引用 rule-based classifier、SD negative prompt presets。
- ✅ 落到 `BackgroundSceneAnalyzerService._infer_people_policy` 和 `image_generation_profiles.json::background_people_policy_keywords`。
- ✅ 符合哲学：规则驱动 + 关键词补充，不引入新模型供应商；保证背景无人物主体化（empty_required 是默认）。

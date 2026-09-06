# A05 — background-scene-no-plot-contamination

## 背景

经验证：即使 system prompt 写了"不要输出剧情"，GLM-4 系列仍会在 `environment_description` 里偷偷塞剧情动词（"林夜发现 / 苏晚晴到达 / 苏墨赴约"）。这是中文叙事 LLM 的固有惯性——它把"环境描述"理解为"场景里发生了什么"。

参考用户反馈（历史会话）：用户明确否定"事后删人物相关词"的黑名单 strip 思路，要求"让 LLM 对本章场景进行分析，针对场景给出生成背景的 prompt"。但即便如此，仍需要在 LLM 输出后加**最后一道反剧情保险**，避免极端 case 漏出。

## SOTA 实践

**OpenAI Evals 框架**（[`github.com/openai/evals`](https://github.com/openai/evals)）的 testing_criteria 模式：每条测试断言"输出必须不含 X"或"输出必须含 Y"。

**Anthropic prompt engineering "explicit negative constraints"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/system-prompts`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/system-prompts)）：单纯说"don't do X"无效，必须配正反对照例。

**中文 NLP 关键词检测**（jieba / pkuseg）已成熟，可作为后置校验工具。

## 落地建议

**system prompt 层**（治本）：

```
<Rules>
严禁输出以下内容（任何一项命中即整体 reject）：
- 故事概要 / 章节摘要 / 情节回顾
- 对白转述（"他说" / "她回答" / 直接引号）
- 人物动作（"X 走向 Y" / "X 拔剑"）
- 人物心理（"X 思考" / "X 意识到"）
- 角色关系（"X 与 Y 对峙" / "X 怀疑 Y"）
- 命名角色名（X 出现在 environment_description 中）
</Rules>

<AntiExample>
[错误] environment_description: 林夜推开太守府大门，发现庭院中...
[正确] environment_description: 太守府庭院，正午阳光直射，青砖地面有苔痕，东西两厢为木结构歇山顶建筑，檐角挂有铜铃...
</AntiExample>
```

**后置校验层**（治标兜底）—— 落到 `BackgroundSceneAnalyzerService._validate_no_plot(specs)`：

```python
PLOT_KEYWORDS_ZH = {
    "主角", "发现", "到达", "看见", "听见", "意识到", "思考", "怀疑",
    "走向", "拔剑", "对峙", "冲向", "跪下", "死去", "死去", "鲜血",
    "被", "杀", "咽喉", "赴约", "交易", "对决",
}
NAMED_CHAR_NAME_PATTERN = re.compile(r"[一-鿿]{2,4}")  # 简化版

def _validate_no_plot(self, specs: list[BackgroundSceneSpec]):
    for spec in specs:
        # 1. environment_description 不能含命名角色
        for char in spec.forbidden_characters:
            if char in spec.environment_description:
                raise PlotContaminationError(
                    f"scene {spec.scene_id}: forbidden_character '{char}' leaked into description"
                )
        # 2. environment_description 不能含剧情关键词
        for kw in PLOT_KEYWORDS_ZH:
            if kw in spec.environment_description:
                raise PlotContaminationError(
                    f"scene {spec.scene_id}: plot keyword '{kw}' in description"
                )
```

命中即 raise，触发 `_fallback_to_segmenter`。

## 风险与权衡

1. **黑名单覆盖不全**：用户已否定黑名单思路；这里的 `_validate_no_plot` 只是兜底，**不依赖它做主路径**。权衡：把黑名单做小（10-20 个最高频剧情词），把主要防线放在 system prompt + LLM 蒸馏。
2. **误伤合理环境词**："鲜血"在描述"战场遗迹"时是合法环境词。权衡：用更具体的复合关键词（"鲜血淋漓" / "鲜血飞溅"），避开单词匹配。

## 完成判据自检

- ✅ 围绕"反剧情污染"，不跑题到切场景或 schema。
- ✅ 引用 OpenAI Evals、Anthropic prompt engineering 指南。
- ✅ 落到 `BackgroundSceneAnalyzerService._validate_no_plot` 和 system prompt `<Rules>` 区。
- ✅ 符合哲学：双层防线（system prompt 为主 + 后置校验为辅），主路径仍是 LLM schema 抽取。

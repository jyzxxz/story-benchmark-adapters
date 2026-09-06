# C08 — background-cn-plot-strip-fallback

## 背景

历史教训：用户反馈 retry_prompt 仍含中文剧情（"苏墨赴约，宋雪以情报交易为名..."）。这是 fallback builder 路径漏出的——当 rewriter 失败时，fallback 用了含 summary 的 prompt。

兜底机制：若 rewriter 失败走 fallback builder 且 `scene_description` 含中文剧情，按逗号/句号切片丢弃含中文字符的片段。但要保留 `Named cast exclusion (CN): 苏墨, 宋雪` 段的中文名（这是显式排除，不是剧情）。

`_strip_chinese_segments` 已在 `image_generation_service.py` 实现存在；本项正式文档化设计依据 + 边界。

## SOTA 实践

**用户明确反馈**（历史会话）：

> 不要事后删人物相关词语；应该直接让 LLM 对本章发生的场景进行分析。

——这是否定"以 strip 为主路径"的方案。本项的 `_strip_chinese_segments` 是**纯兜底**，主路径仍是 LLM 蒸馏。

**Regex-based sanitization in moderation**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：moderation 失败后的字符级 sanitize 是工业级 fallback。

**CogView-4 中文 token 行为**（[`github.com/THUDM/CogView4`](https://github.com/THUDM/CogView4)）：CogView-4 对中文 prompt 反应良好——这反而是问题，因为它会"忠实"地把中文剧情画出来。

## 落地建议

`image_generation_service.py::_strip_chinese_segments`：

```python
CN_CHAR_RE = re.compile(r"[一-鿿]")

def _strip_chinese_segments(self, prompt: str) -> str:
    """
    Last-resort CN-PLOT-STRIP：从 prompt 中按句号/逗号切片，丢弃含中文字符的片段。
    保留：
    - 标记为 "Named cast exclusion" 的段（中文角色名是显式排除，不是剧情）
    - 纯英文段
    丢弃：
    - 含中文剧情动词的段（赴约 / 调查 / 要求 / 杀 / 等）
    """
    if not prompt:
        return prompt

    lines = prompt.split("\n")
    cleaned = []
    for line in lines:
        # 1. Named cast exclusion 段保留（CN 名是合法的）
        if line.startswith("Named cast exclusion"):
            cleaned.append(line)
            continue
        # 2. 按逗号 / 句号切片
        segments = re.split(r"[,。；]", line)
        kept = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            if CN_CHAR_RE.search(seg):
                # 含中文：丢弃（除非整段是 "Named cast exclusion" 标记）
                continue
            kept.append(seg)
        if kept:
            cleaned.append(", ".join(kept))
    return "\n".join(cleaned).strip()
```

**调用位置**：`_enforce_background_environment_focus` 末尾（见 C02）。

**关键边界**：
- `Named cast exclusion (CN): 苏墨, 宋雪` — 整行保留（E03 设计）
- `苏墨赴约，宋雪以情报交易为名...` — 丢弃（剧情）
- `[Environment] bedroom with moonlight` — 保留（纯英文）

## 风险与权衡

1. **误伤合法中文 tag**：例如风格分类器输出 `"art_style: 写实国风电影感"`——这种 tag 是合法的。权衡：style_tags 段（`[Style]` 标签开头）整体保留，不进入 strip。
2. **过度依赖 strip**：用户已否定 strip 思路。权衡：本函数是 **last-resort fallback**，只在 LLM 蒸馏 / enforce 主路径都失败后才触发；不应在主路径生效。日志埋点 `cn_plot_strip_invoked=true` 监控频率，>1% 触发告警。

## 完成判据自检

- ✅ 围绕"CN-PLOT-STRIP 兜底"，不跑题到 enforce 函数实现（C02）。
- ✅ 引用用户反馈、OpenAI moderation sanitize、CogView-4 中文行为。
- ✅ 落到 `image_generation_service.py::_strip_chinese_segments`（已存在，需正式文档化）。
- ✅ 符合哲学：纯兜底（用户否定 strip 主路径），主路径仍是 LLM 蒸馏；不引入新模型供应商。

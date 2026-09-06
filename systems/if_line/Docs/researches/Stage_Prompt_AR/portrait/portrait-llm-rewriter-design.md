# A01 — portrait-llm-rewriter-design

> 把 `build_portrait_prompts` 的字段拼贴升级为 LLM 重写路径。本项依赖 D01 架构。

## 背景

当前 portrait prompt 链路：

```
StoryBible.characters[].appearance (中文)
  ↓
prompt_builder_service.build_portrait_prompts:76
  ↓ 组装 dict {character_id, name, appearance_prompt(中文), emotion, outfit, pose, genre, seed}
image_generation_service.generate_portrait:413
  ↓ 调 _build_portrait_prompt
image_generation_service._build_portrait_prompt:498
  ↓ 用英文模板把中文 appearance 塞进 appearance_prompt 槽位
  ↓ 在头部塞 200 token 否定词
最终 prompt (1800+ 字符)
  ↓
CogView-4
```

**真实问题示例**（从 `_build_portrait_prompt:543-557`）：

```
ABSOLUTELY NO TEXT WHATSOEVER, NO letters, NO words, NO Chinese characters,
NO Japanese kanji, NO captions, ... (200 token 否定词),
masterpiece, best quality, ultra detailed,
half body shot from waist up, character centered,
Chinese historical style, hanfu,
身着素白丧服的年轻女子,           ← 中文原样塞进英文槽
expression: neutral expression, calm face,
wearing ,
standing pose, ...
```

CogView-4 对中英混杂的注意力分散，关键外貌细节（素白丧服、年轻女子）权重被稀释；200 token 否定词抢占前部注意力位置。

## SOTA 实践

### NovelAI 的 prompt 结构
NovelAI/NAI 的 prompt 工程共识（[Discord 社区 convention](https://docs.novelai.net/image/promptguide.html)）：
- 主体在前 30 token
- 风格在中段
- 负面词放后部（或独立 negative prompt 字段，CogView-4 不支持）
- 单 prompt 控制在 75-100 token（T5 类模型可放宽）

### Midjourney 的 natural language prompt
Midjourney V6 显著**降低了**关键词堆砌的有效性，更倾向完整句子的自然语言描述。这暗示扩散模型的整体趋势——LLM 重写的自然语言 prompt 在多模态模型上表现更好。

### DALL-E 3 的"GPT-4 重写"
OpenAI 在 DALL-E 3 强制 GPT-4 重写用户 prompt，输出格式就是**主语优先的自然语言段落**，而非逗号分隔关键词。

## 落地建议

### 输入 / 输出 schema

**输入**（沿用 D01 的 `fields` dict）：
```python
fields = {
    "asset_type": "portrait",
    "character_id": "abc123",
    "name": "李清照",
    "appearance_zh": "身着素白丧服的年轻女子，眉目清丽，肤色白皙",
    "emotion": "sad",          # 已枚举
    "outfit": "mourning",      # 已枚举
    "pose": "standing",        # 已枚举
    "genre": "historical",
    "shot": "half_body",       # 新增：half_body | full_body
    "target_token_budget": 100 # 新增
}
```

**输出**（D01 的 `RewrittenPrompt`）：
```python
RewrittenPrompt(
    subject="young Chinese woman in her early twenties, wearing flowing white mourning hanfu with crossed collar, pale porcelain complexion, slender figure, delicate brows and clear eyes, somber downcast gaze",
    details=[
        "loose black hair partially pinned with a single white jade hairpin",
        "soft folds of unadorned white silk cascading to the floor",
        "pale skin with subtle shading on cheeks",
        "subtle restrained sorrow in her expression"
    ],
    lighting="soft diffused overcast lighting from the side, gentle highlights on silk folds",
    composition="half body shot from waist up, character centered, slight three-quarter angle",
    style="Tang dynasty Chinese painting aesthetic, galgame visual novel character art, clean cel-shading, official art quality",
    negative_minimal=["text", "watermark", "background scene"]
)
```

序列化后单行 prompt ~100 token，主体在前 30 token，符合 NAI 结构。

### 与 `_build_portrait_prompt` 的关系

不在 `_build_portrait_prompt` 内部做 LLM 调用——保持它是纯函数。LLM 调用放在 builder 上层：

```python
# prompt_builder_service.py 修改
async def build_portrait_prompts(self, story_bible, project_id, variations=None):
    prompts = []  # 旧逻辑产出 dict 列表
    # ... 原字段提取 ...

    # 新增：批量为每个 prompt 调用 rewriter
    rewritten = await rewriter.rewrite_many(
        "portrait",
        [{"character_id": p["character_id"], ...} for p in prompts]
    )
    for p, r in zip(prompts, rewritten):
        p["final_prompt"] = r.to_cogview_prompt() if r else None
    return prompts

# image_generation_service.py:413 generate_portrait
async def generate_portrait(self, ...):
    # 优先用 final_prompt
    if final_prompt := prompt_data.get("final_prompt"):
        result = await self.generate_image(prompt=final_prompt, ...)
    else:
        # fallback 旧路径
        result = await self.generate_image(prompt=self._build_portrait_prompt(...), ...)
```

### `_build_portrait_prompt` 的清理

rewriter 上线后，`_build_portrait_prompt:498-559` 的两段不需要再生成：
- `no_text_forbidden`（200 token 否定词） → 改为 rewriter 输出的 `negative_minimal`（5 词）
- `forbid`（题材禁止项） → 合并到 rewriter system prompt

但**不删除** `_build_portrait_prompt`——它作为 fallback 路径保留（D04）。

### seed / cache 影响

- seed 仍由 `prompt_builder_service.generate_seed` 生成（不变）
- 缓存 key 由 `image_generation_service._get_cache_key:80` 计算，目前基于 `prompt` 字符串 hash。rewriter 上线后 `prompt` 变了，缓存自动 miss 一次，之后正常。**不需要改 cache key 算法**（A10 详述）。

## 风险与权衡

1. **失败时回退质量**: LLM 偶尔输出 JSON 解析失败，fallback 走旧 builder，质量回到现状。可接受。
2. **变体矩阵扩展**: A11 后变体从 3 个扩到 9 个时，LLM 调用量翻 3 倍。需要 D07 (batch mode) 配合。
3. **英文风格漂移**: 不同 LLM 输出风格不一致（GLM vs DeepSeek）。建议锁定单一模型（D05 推荐 GLM-4-flash），并在 system prompt 里用 few-shot 锚定风格（D02）。

## 完成判据自检

- [x] 围绕"portrait 的 LLM rewriter 设计"，不跑题到 background/keyframe
- [x] 引用 SOTA：NovelAI prompt guide、Midjourney V6 趋势、DALL-E 3 重写范式
- [x] 给出具体代码位置和修改建议
- [x] 符合哲学：LLM 重写、不引入新图像供应商、不依赖 VLM

## 依赖

- 硬依赖：D01 (架构)
- 软依赖：D02 (system prompt)、D03 (schema)、D04 (fallback)

# C01 — keyframe-llm-rewriter-design

> `build_keyframe_prompts` 字段拼贴升级为 LLM 重写。依赖 D01。

## 背景

当前 keyframe prompt 链路：

```
ChapterOutline.{conflict, scene, emotion, title, summary, visual_keywords} + StoryBible.characters[].appearance
  ↓
prompt_builder_service.build_keyframe_prompts:202
  ↓ _extract_action_from_conflict:279  # {"打": "fighting", "杀": "attacking"} 单字映射
  ↓ _extract_characters_for_keyframe:304  # 取 outline.characters[:3] 名字
  ↓ _find_character_appearance:325  # 从 StoryBible 取中文 appearance
  ↓ 拼 dict {event_name, scene_description(中文), characters[], action(单词), emotion, genre}
image_generation_service.generate_keyframe:650
  ↓ 调 _build_keyframe_prompt
image_generation_service._build_keyframe_prompt:703
  ↓ 角色外观（中文）拼接 → chars_text
  ↓ 套模板 "masterpiece, best quality, cinematic shot, dramatic scene, ..."
最终 prompt
```

**真实问题**（`_build_keyframe_prompt:731-742`）：

```
masterpiece, best quality, ultra detailed,
cinematic shot, dramatic scene,
Chinese historical style, hanfu,
身着玄色铠甲的少年将军，身形挺拔，眉目凌厉,   ← 中文角色 A
身着素白丧服的年轻女子,                       ← 中文角色 B
fighting,                                       ← 单字硬编码动作
intense expression, dramatic,
朝堂,                                           ← 中文场景
dramatic lighting, depth of field, dynamic composition, action shot,
visual novel CG art, official game art quality,
no text, no watermark
```

四类"图与文不符"：

1. **动作映射过度简化**: `_extract_action_from_conflict:286` 用 `{"打": "fighting", "杀": "attacking"}`，一个字决定动作。"少年将军持剑怒视" → 没命中关键词 → 默认 `confronting each other`，画面变成两人对峙，丢失"持剑"。
2. **多角色拼接而非互动**: `chars_text` 只是 A 外观 + B 外观的字符串拼接，没有"A 持剑指向 B，B 后退"这种关系。
3. **中文混杂**: 同 portrait/background 问题。
4. **场景与角色无关联**: 朝堂、人物、动作三段独立，画面里人物可能站在虚空里。

## SOTA 实践

### Visual Novel CG 的 prompt 结构（Galgame 行业实践）
商业 VN 工作室（Key, Type-Moon）的 CG 资源管理惯例：每张 CG 必须显式描述 (a) 场景 (b) 每个角色的位置和动作 (c) 角色间关系 (d) 摄像机角度。LLM 重写应输出符合这个结构的描述。

### SDXL 的 "regional prompting"
[Regional Prompter](https://github.com/hako-mikan/sd-webui-regional-prompter) 让 prompt 不同部分作用于图像不同区域。CogView-4 不支持原生区域 prompt，但**LLM 可以输出"前景-中景-背景"分层的英文描述**，达到类似效果——模型隐式学会区分。

### Action prompt 的动词强度
[Sakimichan style prompts](https://www.deviantart.com/sakimichan) 社区惯例：用 `clashing swords, sparks flying` 而不是 `fighting`；用 `lunging forward with blade extended` 而不是 `attacking`。视觉强动词显著改善动态感。这就是 C08 要解决的问题。

## 落地建议

### 输入 schema

```python
fields = {
    "asset_type": "keyframe",
    "event_zh": "标题或事件名",                # 从 ChapterOutline.title
    "scene_zh": "朝堂",                        # 场景
    "conflict_zh": "少年将军持剑怒视着...",     # 关键输入，rewriter 从这里抽动作
    "summary_zh": "...",                       # 兜底
    "characters": [
        {"name": "李将军", "role": "aggressor", "appearance_zh": "身着玄色铠甲的少年将军，身形挺拔"},
        {"name": "李清照", "role": "victim", "appearance_zh": "身着素白丧服的年轻女子"}
    ],
    "emotion": "intense",                      # 章节情绪
    "genre": "historical",
    "target_token_budget": 150
}
```

注意 `conflict_zh` 是关键——它包含了 `{"打": "fighting"}` 这种单字映射丢失的所有信息。

### 输出 schema

```python
RewrittenPrompt(
    subject="young male general in black lacquered lamellar armor lunging forward with a drawn bronze sword, pointing the blade toward a woman in white mourning hanfu who steps back with raised hands",
    details=[
        "general stands on the left, three-quarter view, fierce glare, furrowed brows",
        "woman on the right, retreating posture, frightened wide eyes",
        "polished marble floor of an imperial court hall",
        "tall vermilion columns with carved dragons in the background",
        "sparks where the blade catches the light"
    ],
    lighting="dramatic side lighting from a high window, strong rim light on the general's armor",
    composition="medium-wide shot, two-character confrontation, slight low angle for tension",
    style="Tang dynasty Chinese setting, visual novel CG art, painterly anime style, official game art quality",
    negative_minimal=["text", "watermark", "extra characters"]
)
```

关键改进：
- `subject` 是**完整动作叙事**，包含两个角色的关系和位置
- `details` 显式列出每角色位置（left/right）+ 场景元素
- 不再用单字动作映射，直接从 `conflict_zh` 提炼

### 与 `_extract_action_from_conflict` 的关系

rewriter 上线后，`prompt_builder_service.py:279-302` 的硬编码映射**保留作为 fallback**，但主路径绕过它。如果 rewriter 失败，fallback 仍能产出可用（但弱）的 prompt。

### 与 `_build_keyframe_prompt` 的关系

`image_generation_service.py:703-744` 的纯函数保留作为 fallback。新增字段 `prompt_data["final_prompt"]` 优先使用。

### 多角色一致性（关联 C03）

rewriter system prompt 应明确要求：
- 角色最多 3 个，每个有独立位置（left/center/right）
- 每个角色的 appearance 必须完整翻译（不只是名字）
- 角色间必须有显式互动描述

这超出 D01 的通用 rewriter，需要 keyframe 专属的 system prompt（D02 在系统 prompt 里通过 `asset_type` 分支实现）。

## 风险与权衡

1. **复杂场景 token 超限**: 3 个角色 + 复杂动作容易超 150 token。**对策**: 设硬上限 200 token，超过时 rewriter 自行删减 details。
2. **角色一致性**: LLM 翻译 appearance 时，同一角色在 keyframe 和 portrait 中可能描述不一致。**对策**: rewriter 应该缓存每个 character_id 的英文 appearance（D06），跨 asset_type 复用。
3. **CogView-4 多人物弱**: CogView-4 在 >2 人时一致性下降（社区共识）。**对策**: keyframe 默认限 2 角色，第 3 角色作为背景虚化。

## 完成判据自检

- [x] 围绕"keyframe 的 LLM rewriter 设计"
- [x] 引用 SOTA：商业 VN CG 惯例、Regional Prompter、Sakimichan 动作动词
- [x] 指出当前代码具体问题（`_extract_action_from_conflict:286` 单字映射、`_build_keyframe_prompt:720` 角色拼接）
- [x] 符合哲学：LLM 重写、不引入新供应商、不上 VLM

## 依赖

- 硬依赖：D01 (架构)、D02 (system prompt 含多角色 few-shot)
- 软依赖：C02 (action extraction)、C03 (multi-character coherence)、C06 (角色 appearance 跨资产一致)

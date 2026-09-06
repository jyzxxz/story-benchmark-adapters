# B01 — background-llm-rewriter-design

> `build_background_prompts` 字段拼贴升级为 LLM 重写。依赖 D01。

## 背景

当前 background prompt 链路：

```
ChapterOutline.{scene, visual_keywords, summary}  (中文)
  ↓
prompt_builder_service.build_background_prompts:120
  ↓ 关键词兜底匹配 ("帐中/营帐/战场/...") 抽 scene
  ↓ 拼接 scene + visual_keywords → scene_description
image_generation_service.generate_background:561
  ↓ 调 _build_background_prompt
image_generation_service._build_background_prompt:614
  ↓ 头部塞 "ZERO PEOPLE, ZERO FIGURES, no humans, no people..." (50+ token)
  ↓ 检查 soldier_co_location_words → 加更多否定词
最终 prompt
```

**真实问题**（`_build_background_prompt:625-646`）：

```
COMPLETELY DESERTED SCENE, ZERO PEOPLE, ZERO FIGURES, no humans,
no people, no characters, no figures, empty scene, deserted, uninhabited,
Wide cinematic establishing shot, scenic environment view, 16:9 aspect,
Chinese historical style, hanfu, traditional Chinese clothing,
朝堂,                    ← 中文场景词
,...
lighting and atmosphere: bright sunlight, clear blue sky,
masterpiece anime visual novel background art, Makoto Shinkai inspired,
```

三类"图与文不符"：

1. **中文场景词原样塞**: "朝堂"在 CogView-4 上可能画出无关场景。CogView-4 是中文训练但中英混杂 prompt 的注意力分散。
2. **`hanfu` 错误带入背景**: genre style "Chinese historical style, **hanfu**, traditional Chinese clothing" 被注入背景 prompt——但背景不应该有人穿衣服。这是 `_build_background_prompt:640` 错误复用了 portrait 的 genre style。
3. **否定词堆砌污染**: "ZERO PEOPLE" 一长串，CogView-4 注意力机制对负面词的处理不是简单"避开"——它可能反而强化"people"特征。

## SOTA 实践

### Makoto Shinkai 风格 SD prompt (Civitai)
Civitai 上 Shinkai 风格背景的成熟 prompt 模板（[参考](https://civitai.com/models/39425)）：
```
no humans, scenery, sky, clouds, distant mountains, ...
```
注意：**`no humans` 一词即可**，不需要"ZERO FIGURES, ZERO PEOPLE, no characters, no figures, ..."。重复否定词对 SD/CogView 系无益甚至有害。

### Scene prompt 的"空间-时间-氛围"三段式
VN 游戏背景资源库 (Komorebi / FSM Background Pack) 的命名规范暗示了 prompt 结构：
- 空间：`courtyard`, `hall`, `street`
- 时间：`day`, `dusk`, `night`
- 氛围：`rainy`, `foggy`, `peaceful`

rewriter 应该输出符合这三段式的描述。

### CogView-4 的中文理解优势
[CogView-4 技术报告](https://arxiv.org/abs/2503.02513)（智谱 AI）明确指出：CogView-4 的中文理解能力优于同期 SDXL，但**中英混杂 prompt 的效果差于纯英文或纯中文**。结论：rewriter 应统一输出纯英文。

## 落地建议

### 输入 schema

```python
fields = {
    "asset_type": "background",
    "scene_zh": "朝堂",                       # 从 ChapterOutline.scene
    "visual_keywords": ["金色琉璃瓦", "雕龙柱", "..."],  # 从 visual_keywords
    "summary_zh": "...",                       # 兜底
    "mood": "day",                             # 已枚举
    "genre": "historical",                     # 已枚举
    "story_bible_worldview": "...",            # 用于推断题材
    "needs_no_humans": True,                   # LLM 判断 (见 B06)
    "target_token_budget": 120
}
```

### 输出 schema

```python
RewrittenPrompt(
    subject="vast imperial court hall with towering vermilion columns carved with coiling dragons, polished marble floor reflecting golden sunlight, ornate coffered ceiling with painted clouds",
    details=[
        "empty throne at the far end elevated on a jade platform",
        "rows of bronze censers lining the central carpet",
        "tall lattice windows casting geometric shadows",
        "no furniture in the foreground, open ceremonial space"
    ],
    lighting="bright daylight streaming through side windows, warm golden ambient glow",
    composition="wide cinematic establishing shot, 16:9, low angle emphasizing hall grandeur",
    style="Tang dynasty Chinese imperial architecture, Makoto Shinkai inspired anime background art, ultra detailed environment",
    negative_minimal=["people", "characters"]
)
```

注意：
- `negative_minimal` 只 2 词（people, characters），不需要 "ZERO FIGURES, ZERO PEOPLE, uninhabited..."
- `style` 段**移除 hanfu**——背景不该有衣服
- `subject` 是空间描述，主体是建筑/环境，不是事件

### 修复 `_build_background_prompt:640` 的 genre style 污染

`image_generation_service.py:640` 当前注入：
```python
{style_prompt}        # 来自 profiles["genre_families"][genre]["style_prompt"]
                      # 内含 "hanfu, traditional Chinese clothing"
```

这应该改为查询**背景专用**的 style_prompt（profiles 应拆分 `genre_families[genre]["style_prompt_environment"]` 和 `style_prompt_character`）——但 AR 阶段不实现这个，留给后续 execution。rewriter 上线后，system prompt 里告知"这是背景图，禁止服装词"即可绕过。

### soldier_co_location 处理

`profiles["soldier_co_location_words"]` 是 hardcoded 列表（14 词）。rewriter 应该接收 `needs_no_humans: bool`，由 LLM 在 builder 层判断（B06）。AR 阶段保持 hardcoded fallback，rewriter 优先。

### 调用点

```python
# prompt_builder_service.py:120 build_background_prompts 末尾
for p in prompts:
    rewritten = await rewriter.rewrite("background", {
        "scene_zh": p["scene_description"],
        "mood": p["mood"],
        "genre": p["genre"],
        ...
    })
    if rewritten:
        p["final_prompt"] = rewritten.to_cogview_prompt()
```

## 风险与权衡

1. **`needs_no_humans` 误判**: LLM 判断"该不该出现人物"有概率错。比"无脑堆否定词"风险高但更精细。**对策**: 关键场景（朝堂、宫殿）保留 hardcoded fallback；其他交给 LLM。
2. **建筑词汇不足**: LLM 不知道"朝堂"具体长什么样（缺训练数据）。**对策**: 在 system prompt 里提供 Tang/Song/Ming 三个朝代建筑风格 few-shot 示例（D02）。
3. **多氛围变体**: 同一场景生成 day/night/rain 三个变体时，rewriter 应该接收"基础场景 + 差异 mood"，避免重新生成整个空间描述（B09）。

## 完成判据自检

- [x] 围绕"background 的 LLM rewriter 设计"
- [x] 引用 SOTA：Civitai Shinkai 风格模板、CogView-4 技术报告
- [x] 指出当前代码具体 bug（`_build_background_prompt:640` genre style 含 hanfu）
- [x] 符合哲学：LLM 重写、不引入新供应商

## 依赖

- 硬依赖：D01 (架构)、D02 (system prompt 含建筑 few-shot)
- 软依赖：B03 (no-humans 策略)、B05 (建筑词汇)、B06 (LLM 判断 needs_no_humans)

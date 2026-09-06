# B02 — background-style-taxonomy-config

## 背景

`style_tags` 必须从受控词表选，否则 LLM 会自造词或飘到无关维度。词表设计要点：
1. 5 维度（art_style / color_palette / lens / texture / mood）覆盖视觉的"形式"和"情绪"
2. 每个 genre 有专属词表，避免古风场景选了 cyberpunk tag
3. 每维 5-10 个标签，既够选择又不过载

当前 `image_generation_profiles.json` 有 `genre_styles` 但**没有按维度切分**，无法支撑新分类器。

## SOTA 实践

**Stable Diffusion Prompt Book**（[`google/prompting-at-the-museum`](https://google.github.io/prompting-at-the-museum/)）：把 prompt 拆为"风格 / 主题 / 构图 / 光照 / 情绪"5 维度，每维度独立调参，可视化对比。

**Danbooru tag system**（[`github.com/danbooru/danbooru`](https://github.com/danbooru/danbooru)）：tag 按 category（artist / character / copyright / general / meta）分类，证明"按维度组织 tag 库"是图像 prompt 工程的通行做法。

**Midjourney style reference**（[`docs.midjourney.com/docs/style-reference`](https://docs.midjourney.com/docs/style-reference)）：分 art_style / camera / lighting / mood 多个维度独立调权重，是工业级 prompt 系统的标准结构。

## 落地建议

`image_generation_profiles.json` 新增 `background_style_taxonomy`：

```json
"background_style_taxonomy": {
  "art_style": {
    "historical": ["写实国风电影感", "写意水墨", "复古工笔", "古风厚涂", "宋代山水"],
    "modern": ["写实电影感", "纪实摄影", "新海诚写实", "复古胶片"],
    "scifi": ["赛博朋克", "硬科幻概念", "蒸汽朋克", "low-poly"],
    "fantasy": ["西方奇幻厚涂", "黑暗奇幻", "魔幻写实", "童话插画"],
    "anime": ["新海诚动画", "京阿尼清新", "吉卜力水彩", "二次元厚涂"]
  },
  "color_palette": {
    "historical": ["青灰冷调", "暖金棕", "暮色紫灰", "墨色为主"],
    "modern": ["冷蓝", "暖黄", "雨夜霓虹", "灰白极简"],
    "scifi": ["金属蓝", "霓虹紫粉", "深空黑", "电浆青"],
    "fantasy": ["魔法蓝紫", "暗金", "苔藓绿", "血红与金"],
    "anime": ["晨光暖", "雨后清新蓝绿", "黄昏橘紫", "夜空深蓝"]
  },
  "lens_or_camera_feel": {
    "all": ["24mm广角establishing", "35mm环境广角", "高机位远景", "室内一体化广角", "长焦压缩远景", "微距近景"]
  },
  "texture_or_rendering": {
    "all": ["潮湿反光", "木质旧化", "石材风化", "空气透视", "细雾颗粒", "油画厚涂", "水彩晕染"]
  },
  "mood": {
    "all": ["静谧", "萧索", "压迫", "冷清", "紧张前夜", "潮湿阴冷", "温暖怀旧", "诡异", "壮阔", "孤寂"]
  }
}
```

**关键约束**：
- `art_style` 和 `color_palette` 按 genre 分桶（古风不会选到赛博朋克）
- `lens / texture / mood` 共享词表（这些维度跨 genre 通用）
- 每维 4-10 个标签，避免选择过载

`BackgroundStyleClassifierService._validate_in_taxonomy(tag, genre)` 强制：如果 tag.value 不在该 genre 的对应 dimension 词表中，raise ValidationError。

## 风险与权衡

1. **词表覆盖不全 → LLM 选不到合适的**：例如"赛博朋克 + 古风"跨界项目无词可选。权衡：在 genre_overrides 节点支持"主 genre + 兼容副 genre"，例如 `historical` 兼容 `fantasy` 的部分 art_style。
2. **每维词表过大 → LLM 注意力分散**：>10 个 tag 让 LLM 选反而错。权衡：每维严格 ≤10，必要时拆分子维度。

## 完成判据自检

- ✅ 围绕"5 维受控词表"，不跑题到 system prompt 或 coverage rule。
- ✅ 引用 SD Prompt Book、Danbooru tag system、Midjourney style reference。
- ✅ 落到 `image_generation_profiles.json::background_style_taxonomy`。
- ✅ 符合哲学：受控词表约束 LLM，按 genre 区分避免 anime 硬编码问题重现。

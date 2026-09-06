# 立绘生成与背景图生成 — 需求规格文档

> 本文档基于 xuxie_agent 项目实际情况定制，结合 StoryBible 人物设定、Asset 模型和 VNGraph 流程。
> 文档可直接作为开发/迁移/验收依据。

---

## 1. 总体概述

### 1.1 项目背景

本系统为 xuxie_agent 项目提供视觉素材自动生成能力，用于 AVG/galgame 风格的交互阅读平台。

**现有系统架构**：
- **后端**：FastAPI + SQLite + SQLAlchemy
- **前端**：Vue 3 + Element Plus + TypeScript
- **数据模型**：Project → StoryBible → ChapterOutline → ChapterContent → Asset → VNGraph
- **工作流引擎**：状态机模式管理项目进度

### 1.2 核心模块

| 模块 | 职责 | 输出 | 与现有系统集成 |
|---|---|---|---|
| **立绘生成（Portrait）** | 生成透明背景的单角色立绘 | 透明 PNG（RGBA 32-bit） | 存入 Asset 表 (asset_type='character') |
| **背景图生成（Background）** | 生成满屏环境背景图 | 实色 PNG（不透明） | 存入 Asset 表 (asset_type='background') |
| **关键帧生成（Keyframe）** | 生成剧情关键时刻插图 | 实色 PNG | 存入 Asset 表 (asset_type='keyframe') |

### 1.3 VNGraph 布局关系

```
┌──────────────────────────────────────────┐
│            背景图（BackGroundNode）        │  ← asset_type='background'
│                                          │
│       ┌──────────────┐                   │
│       │  角色立绘       │                  │  ← asset_type='character'
│       │ （TachiNode）   │                  │     TachiID = character_id
│       └──────────────┘                   │
│                                          │
│ ┌────────────────────────────────────┐   │
│ │          ParagraphNode             │   │  ← DialogueLine 包含 SpeakerId
│ │          对话 / 旁白                │   │
│ └────────────────────────────────────┘   │
└──────────────────────────────────────────┘
```

---

## 2. 与现有数据模型的集成

### 2.1 Asset 模型扩展

现有 Asset 模型字段：
```python
class Asset(Base):
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    chapter_index = Column(Integer)
    asset_type = Column(String(50))  # character / background / keyframe
    target_name = Column(String(200))
    prompt = Column(Text)
    image_url = Column(String(500))
    status = Column(String(50), default="pending")
```

**扩展字段建议**：
```python
# 新增字段（通过 Alembic 迁移添加）
character_id = Column(String(100))  # 关联 StoryBible.characters[].name
emotion = Column(String(50))         # 情绪变体
outfit = Column(String(50))          # 装束变体
pose = Column(String(50))            # 姿态变体
mood = Column(String(50))            # 背景氛围（仅 background）
scene_location = Column(String(200)) # 场景地点（仅 background）
seed = Column(Integer)               # 生成种子（一致性保证）
generation_params = Column(JSON)     # 完整生成参数（用于复现）
```

### 2.2 StoryBible 人物设定集成

现有 StoryBible.characters 结构：
```json
{
  "name": "荆轲",
  "role": "主角",
  "appearance": "二十出头，剑眉星目，身形修长，",
  "personality": "冷静克制，内心炙热",
  "motivation": "为天下苍生刺秦",
  "voice": "语气冷静克制，常用简短判断句..."
}
```

**用于立绘生成的字段**：
- `name` → character_id（唯一标识）
- `appearance` → 外貌描述（必需）
- `personality` → 表情参考
- `role` → 确定主角/配角权重

### 2.3 ChapterOutline 场景集成

现有 ChapterOutline 场景相关字段：
```python
scene = Column(String(200))           # 场景描述
visual_keywords = Column(JSON)        # 视觉关键词
```

**用于背景生成的字段**：
- `scene` → 场景地点基础描述
- `visual_keywords` → 光影、氛围、道具关键词

---

## 3. 立绘生成需求规格

### 3.1 功能需求

#### FR-P1：单角色立绘生成

系统接收 StoryBible 中的角色设定，生成**透明背景**的角色立绘 PNG 图片。

**输入参数**：

| 参数 | 来源 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `character_id` | StoryBible.characters[].name | string | 是 | 角色唯一标识 |
| `appearance` | StoryBible.characters[].appearance | string | 是 | 外貌描述（英文） |
| `gender` | 从 appearance/role 推断 | string | 条件 | 性别标记 |
| `emotion` | 请求参数 | enum | 是 | 8 种情绪之一 |
| `outfit` | 请求参数 | enum | 否 | 装束变体 |
| `pose` | 请求参数 | enum | 否 | 姿态变体 |
| `shot` | 请求参数 | enum | 否 | half-body / full-body |
| `story_id` | project.id | number | 是 | 项目 ID |
| `story_bible` | StoryBible.raw_json | object | 是 | 完整设定 |
| `genre_family` | 从 style 推断 | enum | 否 | 题材族 |

**输出**：Asset 记录

```json
{
  "project_id": 1,
  "chapter_index": null,
  "asset_type": "character",
  "target_name": "荆轲",
  "character_id": "荆轲",
  "emotion": "neutral",
  "outfit": "default",
  "pose": "standing",
  "prompt": "masterpiece, 1boy, solo...",
  "image_url": "/assets/portraits/jing_ke/neutral.png",
  "seed": 12345,
  "status": "completed"
}
```

**文件路径规则**：
- 基础路径：`/assets/portraits/<character_id>/`
- 文件名格式：`<outfit>_<pose>_<emotion>.png`
- 默认值省略：`neutral.png` 而非 `default_standing_neutral.png`

#### FR-P2：批量立绘生成

为项目中所有角色生成默认情绪的立绘集合。

**触发时机**：
- 项目状态从 `outline_approved` → `asset_generating` 时自动触发
- 或管理员手动触发

**流程**：
1. 从 StoryBible.characters 获取所有角色
2. 为每个角色生成 `neutral` 情绪的默认立绘
3. 串行执行（避免内存/API限流）
4. 记录生成统计到 GenerationStat

#### FR-P3：立绘变体生成

为特定角色生成多种情绪/装束/姿态的变体。

**场景**：
- 章节生成时根据章节情绪自动触发
- 管理员手动选择变体生成

**示例**：
```
角色：荆轲
需要变体：
- neutral（默认，已生成）
- angry（第3章战斗场景）
- injured（第5章受伤场景）
- armor（第7章决战场景）
```

---

### 3.2 情绪枚举（Emotion）

与现有 StoryBible.emotional_line 和 ChapterOutline.emotion 对齐：

| 枚举值 | 中文 | 面部描述关键词 | 适用场景 |
|---|---|---|---|
| `neutral` | 平静 | calm, relaxed gaze, composed | 默认状态、日常对话 |
| `smile` | 微笑 | subtle restrained smile, closed mouth | 温情、释然 |
| `happy` | 大笑 | bright joyful, laughing | 欢庆、胜利 |
| `angry` | 愤怒 | furrowed brows, gritted teeth, intense glare | 冲突、对峙 |
| `sad` | 悲伤 | downcast eyes, trembling lips | 失落、离别 |
| `cry` | 哭泣 | tears streaming, reddened eyes | 痛失、绝望 |
| `surprise` | 惊讶 | eyes wide open, mouth agape | 意外、揭露 |
| `fear` | 恐惧 | trembling, pale face | 危机、威胁 |

### 3.3 装束枚举（Outfit）

与题材族（GenreFamily）联动：

| 枚举值 | 中文 | historical | modern | sci-fi | fantasy | anime |
|---|---|---|---|---|---|---|
| `default` | 常服 | 汉服/布衣 | T恤/休闲装 | 太空服 | 法袍 | 校服 |
| `armor` | 战甲 | 鳞甲/札甲 | 战术背心 | 动力甲 | 附魔板甲 | 忍者服 |
| `formal` | 正装 | 官服/朝服 | 西装/礼服 | 联邦制服 | 礼仪长袍 | 学生制服 |
| `mourning` | 素服 | 孝服/白衣 | 黑色正装 | 纪念服 | 祭司长袍 | 黑色校服 |
| `bound` | 囚衣 | 枷锁/囚服 | 囚服 | 收容服 | 束缚装 | 绳缚装 |
| `injured` | 伤损 | 染血/绷带 | 医用绷带 | 修复痕迹 | 附魔绷带 | 动漫伤效 |

### 3.4 姿态枚举（Pose）

与 VNGraph TachiNode 动画对齐：

| 枚举值 | 中文 | VNGraph EnterType | 说明 |
|---|---|---|---|
| `standing` | 站立 | FadeIn | 默认站姿 |
| `pointing` | 指向 | SlideInLeft | 指向/质问姿态 |
| `combat` | 战斗 | PopIn | 战斗准备姿态 |
| `kneeling` | 跪伏 | FadeIn | 跪坐/低伏 |
| `reaching` | 伸手 | SlideInBottom | 伸手/抓握 |
| `defensive` | 防御 | FadeIn | 防御姿态 |

### 3.5 题材族（GenreFamily）

从 Project.style 字段推断：

| 枚举值 | 匹配关键词 | 风格特征 |
|---|---|---|
| `historical` | 历史、古代、战国、唐朝、武侠、仙侠 | 中国古风，汉服/甲胄 |
| `modern` | 都市、现代、职场、校园 | 现代写实 |
| `sci-fi` | 科幻、末世、赛博、太空、机甲 | 高科技美学 |
| `fantasy` | 玄幻、奇幻、魔法、异世界 | 混合东西方奇幻 |
| `anime` | 同人、动漫、火影、海贼 | 日式动漫风格 |

---

### 3.6 Prompt 构建规则

立绘 prompt 由以下部分按顺序拼接：

```
1. 景别指令
   - full-body: "FULL BODY SHOT, entire character visible from HEAD to FEET, vertical 3:5 aspect"
   - half-body: "half body shot from waist up, character centered"

2. 文化风格锁定（根据 GenreFamily）
   - historical: "Chinese historical style, hanfu, traditional Chinese clothing, forbidden: Japanese armor, European plate armor"
   - modern: "modern realistic style, contemporary fashion"
   - sci-fi: "sci-fi style, futuristic technology"
   - fantasy: "fantasy style, magical elements"
   - anime: "anime style, Japanese animation"

3. 性别约束
   - 从 appearance 或 role 推断
   - 强制 "an adult MAN/WOMAN"

4. 角色外貌（来自 StoryBible）
   "character appearance: <appearance 翻译为英文>"

5. 装束覆盖（非 default 时）
   "OUTFIT OVERRIDE: <outfit hint>. Replace regular clothing."

6. 表情描述
   "expression: <EMOTION_PROMPTS[emotion]>"

7. 风格模板
   "masterpiece visual novel character art, galgame style, clean cel-shading, crisp line art"

8. 透明背景指令
   "IMPORTANT: pure solid white background, no scenery, no environment"

9. 无文字指令
   "no text, no watermark, no signature"
```

### 3.7 后处理管线

```
AI 模型原始输出（白底 + 水印）
  │
  ├─ 1. 裁水印：底部裁掉指定像素
  │
  ├─ 2. 抠透明背景：rembg 去除白底
  │
  ├─ 3. 格式转换：sharp 强制 RGBA 32-bit PNG
  │
  └─ 4. 存储：
       ├─ 本地开发：保存到 backend/static/assets/portraits/
       └─ 生产环境：上传到 CDN/OSS
```

### 3.8 一致性保证

| 维度 | 机制 | 实现 |
|---|---|---|
| 跨情绪一致 | 同 seed | `seed = hash(character_id + story_id)` |
| 跨装束一致 | 同 seed + 同 appearance | 只变装束描述 |
| 文化风格一致 | GenreFamily 锁定 | 防止风格漂移 |
| 视觉锚点 | 首张立绘提取特征 | 描述注入后续 prompt |

---

## 4. 背景图生成需求规格

### 4.1 功能需求

#### FR-S1：场景背景图生成

系统接收 ChapterOutline 场景信息，生成**不含任何人物**的满屏环境背景图。

**输入参数**：

| 参数 | 来源 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `location_tag` | ChapterOutline.scene | string | 是 | 场景地点标签 |
| `description` | visual_keywords 转 | string | 是 | 英文场景描述 |
| `mood` | ChapterOutline.emotion | enum | 是 | 氛围 |
| `story_id` | project.id | number | 是 | 项目 ID |
| `chapter_index` | 请求参数 | number | 是 | 章节索引 |

**输出**：Asset 记录

```json
{
  "project_id": 1,
  "chapter_index": 3,
  "asset_type": "background",
  "target_name": "燕国宫殿",
  "scene_location": "燕国宫殿",
  "mood": "night",
  "prompt": "masterpiece, scenery, no humans...",
  "image_url": "/assets/backgrounds/chapter_3_palace_night.png",
  "seed": 67890,
  "status": "completed"
}
```

#### FR-S2：场景复用与缓存

**场景复用逻辑**：
1. 同一 story_id + scene_location + mood 组合只生成一次
2. 后续章节引用相同场景时复用已有资产
3. state 变体（如"火起"）生成新图

#### FR-S3：章节批量背景生成

**触发时机**：
- 项目状态 `chapter_generating` → `asset_generating`
- 为所有章节生成对应背景图

---

### 4.2 氛围枚举（Mood）

| 枚举值 | 中文 | 光照/氛围描述 |
|---|---|---|
| `default` | 默认 | natural ambient lighting |
| `day` | 白天 | bright sunlight, clear blue sky |
| `dusk` | 黄昏 | golden hour, warm orange sky |
| `night` | 夜晚 | deep blue moonlit, lanterns glowing |
| `dawn` | 黎明 | soft pink, pale gold, mist |
| `rain` | 下雨 | heavy rain, wet surfaces |
| `snow` | 下雪 | gentle snowfall, white covering |
| `fog` | 雾天 | dense fog, low visibility |

### 4.3 反人物机制（核心约束）

背景图**严禁出现任何人物**。多层防御：

| 层级 | 机制 | 说明 |
|---|---|---|
| L1 | 头部强锁 | `COMPLETELY DESERTED SCENE, ZERO PEOPLE` |
| L2 | 地点反人物 | 对"军帐/朝堂"追加 `structures only` |
| L3 | 描述过滤 | 去除用户描述中的人物词 |
| L4 | 正向替代 | 只描述建筑/天空/植被 |
| L5 | 末尾负向 | `no soldiers, no guards, no silhouettes` |

**士兵高共现地点词表**：
- 军帐、兵营、营地、营寨、校场
- 朝堂、宫殿、城楼、城墙、关卡
- 哨所、烽火台、帅帐、大帐

---

### 4.4 Prompt 构建规则

```
1. 头部强锁
   "COMPLETELY DESERTED SCENE, ZERO PEOPLE, ZERO FIGURES"

2. 镜头景别
   "Wide cinematic establishing shot, 16:9 aspect"

3. 场景描述（来自 ChapterOutline）
   "detailed scene: <visual_keywords 转 英文>"

4. 氛围光照
   "lighting: <MOOD_PROMPTS[mood]>"

5. 风格模板
   "masterpiece anime background, Makoto Shinkai inspired"

6. 反人物指令
   "pure environment, architectural elements only, uninhabited"

7. 末尾负向
   ", (NO humans, NO soldiers, NO silhouettes)"
```

---

## 5. 关键帧生成需求规格

### 5.1 功能需求

#### FR-K1：剧情关键时刻插图

根据章节内容，生成关键戏剧性瞬间的插图。

**输入参数**：

| 参数 | 来源 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `event_name` | LLM 提取 | string | 是 | 事件名称 |
| `description` | LLM 生成 | string | 是 | 英文画面描述 |
| `characters` | ChapterOutline.characters | string[] | 是 | 涉及角色 |
| `story_id` | project.id | number | 是 | 项目 ID |
| `chapter_index` | 请求参数 | number | 是 | 章节索引 |

**输出**：Asset 记录

```json
{
  "project_id": 1,
  "chapter_index": 7,
  "asset_type": "keyframe",
  "target_name": "荆轲刺秦",
  "prompt": "masterpiece, dramatic scene, intense action...",
  "image_url": "/assets/keyframes/chapter_7_assassination.png",
  "status": "completed"
}
```

### 5.2 关键帧类型

| 类型 | 触发条件 | 示例 |
|---|---|---|
| 战斗瞬间 | conflict=动作/战斗 | 拔剑、对决 |
| 情感高潮 | emotion_shift 剧烈 | 诀别、重逢 |
| 揭示时刻 | key_revelation | 秘密揭露 |
| 转折点 | plot_twist | 真相大白 |

---

## 6. API 端点设计

### 6.1 立绘相关

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| POST | `/api/projects/{id}/portraits/generate` | 生成角色立绘 | admin |
| POST | `/api/projects/{id}/portraits/generate-batch` | 批量生成所有角色立绘 | admin |
| POST | `/api/projects/{id}/portraits/{character_id}/variants` | 生成角色变体 | admin |
| GET | `/api/projects/{id}/portraits` | 获取项目所有立绘 | 登录用户 |
| GET | `/api/projects/{id}/portraits/{character_id}` | 获取角色立绘列表 | 登录用户 |
| DELETE | `/api/projects/{id}/portraits/{character_id}` | 删除角色立绘 | admin |

### 6.2 背景图相关

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| POST | `/api/projects/{id}/backgrounds/generate` | 生成章节背景图 | admin |
| POST | `/api/projects/{id}/backgrounds/generate-batch` | 批量生成所有背景 | admin |
| GET | `/api/projects/{id}/backgrounds` | 获取项目所有背景 | 登录用户 |
| GET | `/api/projects/{id}/backgrounds/{location_tag}` | 获取场景背景列表 | 登录用户 |

### 6.3 关键帧相关

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| POST | `/api/projects/{id}/keyframes/generate` | 生成章节关键帧 | admin |
| GET | `/api/projects/{id}/keyframes` | 获取项目所有关键帧 | 登录用户 |

### 6.4 与现有 API 集成

**扩展现有端点**：

```
POST /api/projects/{id}/chapters/{chapter_index}/generate-assets
↓
现有：只生成 prompt
扩展：可选择直接调用图像生成 API
```

**请求参数扩展**：
```json
{
  "generate_prompts": true,
  "generate_images": false,  // 新增：是否直接生成图片
  "portrait_options": {
    "emotions": ["neutral", "smile"],
    "outfits": ["default", "armor"]
  },
  "background_options": {
    "moods": ["default", "night"]
  }
}
```

---

## 7. 工作流集成

### 7.1 状态流转扩展

现有状态：
```
draft_input → bible_generated → outline_generated →
outline_reviewing → outline_approved → chapter_generating →
asset_generating → vn_graph_generating → reading → completed
```

**asset_generating 细化**：
```
asset_generating
├── portrait_generating
├── background_generating
├── keyframe_generating
└── asset_completed → vn_graph_generating
```

### 7.2 自动触发时机

| 触发点 | 动作 |
|---|---|
| outline_approved | 预生成主角默认立绘 |
| chapter_generating 完成 | 为该章生成背景图 |
| asset_generating | 批量生成所有视觉资产 |
| vn_graph_generating | 校验资产完整性 |

---

## 8. 外部依赖

### 8.1 图像生成 API

| 配置项 | 环境变量 | 默认值 |
|---|---|---|
| API Key | `AI_IMAGE_API_KEY` | — |
| API 端点 | `AI_IMAGE_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` |
| 模型名 | `AI_IMAGE_MODEL` | `cogview-4` |

**API 约束**：
- 画面长宽必须是 32 的倍数
- 长宽范围 [512, 2880]
- 总像素数 ≤ 4,194,304（约 4MP）
- 模型会在右下角打水印，需后处理裁掉

### 8.2 背景去除

- 依赖：`rembg`（Python 库）
- 用途：对立绘图片做背景抠图
- 输出格式：RGBA 32-bit PNG

### 8.3 图像处理

- 依赖：`Pillow`（Python）
- 用途：裁水印、格式转换

### 8.4 存储层

| 环境 | 存储 | 说明 |
|---|---|---|
| 开发 | 本地 `backend/static/assets/` | 返回相对路径 |
| 生产 | OSS/CDN | 返回公网 URL |

---

## 9. 数据库迁移

### 9.1 Asset 表扩展

```sql
-- Alembic 迁移脚本
ALTER TABLE assets ADD COLUMN character_id VARCHAR(100);
ALTER TABLE assets ADD COLUMN emotion VARCHAR(50);
ALTER TABLE assets ADD COLUMN outfit VARCHAR(50);
ALTER TABLE assets ADD COLUMN pose VARCHAR(50);
ALTER TABLE assets ADD COLUMN mood VARCHAR(50);
ALTER TABLE assets ADD COLUMN scene_location VARCHAR(200);
ALTER TABLE assets ADD COLUMN seed INTEGER;
ALTER TABLE assets ADD COLUMN generation_params JSON;
```

### 9.2 索引优化

```sql
CREATE INDEX idx_assets_character ON assets(character_id);
CREATE INDEX idx_assets_type_status ON assets(asset_type, status);
CREATE INDEX idx_assets_project_chapter ON assets(project_id, chapter_index);
```

---

## 10. 前端集成

### 10.1 组件扩展

**AssetPromptView.vue 增强**：
- 添加「直接生成图片」按钮
- 显示生成进度条
- 支持选择生成变体

**新增组件**：
- `PortraitGenerator.vue` - 立绘生成器
- `BackgroundGenerator.vue` - 背景生成器
- `AssetGallery.vue` - 资产画廊

### 10.2 API 封装

**新增 frontend/src/api/imageApi.ts**：
```typescript
export const imageApi = {
  generatePortrait(projectId: number, params: PortraitParams),
  generateBackground(projectId: number, params: BackgroundParams),
  generateKeyframe(projectId: number, params: KeyframeParams),
  getPortraits(projectId: number),
  getBackgrounds(projectId: number),
}
```

---

## 11. 环境变量汇总

| 变量 | 用途 | 默认值 |
|---|---|---|
| `AI_IMAGE_API_KEY` | 图像生成 API Key（必填） | — |
| `AI_IMAGE_BASE_URL` | 图像 API 端点 | `https://open.bigmodel.cn/api/paas/v4` |
| `AI_IMAGE_MODEL` | 图像模型名 | `cogview-4` |
| `WATERMARK_CROP_PX` | 水印裁剪像素 | `80` |
| `IMAGE_STORAGE_TYPE` | 存储类型 | `local` |
| `OSS_BUCKET` | OSS Bucket（生产） | — |
| `OSS_ACCESS_KEY` | OSS Access Key | — |
| `OSS_SECRET_KEY` | OSS Secret Key | — |

---

## 12. 已知约束与边界情况

1. **API 不支持 image-to-image**：无法基于已有图微调，一致性靠 prompt + seed
2. **API 不支持 negative_prompt**：抑制指令写进 prompt 正体
3. **强制水印**：需后处理裁掉
4. **并发限制**：立绘批量生成必须串行
5. **文化误判**：题材族推断需关键词白名单
6. **性别偏女性**：anime 模型需显式 gender prefix

---

## 13. 测试验收标准

### 13.1 立绘生成

- [ ] 能根据 StoryBible 角色生成默认立绘
- [ ] 能生成指定情绪变体
- [ ] 同角色跨情绪外观一致
- [ ] 透明背景无白边
- [ ] 裁掉水印

### 13.2 背景生成

- [ ] 能根据章节场景生成背景
- [ ] 生成的背景无人物
- [ ] 同场景跨 mood 建筑结构一致
- [ ] 16:9 比例正确

### 13.3 集成测试

- [ ] 立绘能正确用于 VNGraph TachiNode
- [ ] 背景能正确用于 VNGraph BackGroundNode
- [ ] 资产能被 ImmersiveReaderView 正确加载
- [ ] 生成统计正确记录到 GenerationStat

---

## 附录 A：Prompt 模板示例

### A.1 立绘 Prompt 模板

```
FULL BODY SHOT, entire character visible from HEAD to FEET, vertical 3:5 aspect

Chinese historical style, hanfu, traditional Chinese clothing,
forbidden: Japanese armor, European plate armor, modern clothing

an adult MAN in his early 20s

character appearance: sharp eyebrows, bright starry eyes,
slender build, black hair tied in topknot

expression: calm, relaxed gaze, composed face

OUTFIT OVERRIDE: Chinese lamellar armor, iron scale armor,
Replace regular clothing while keeping face and hair identical.

masterpiece visual novel character art, galgame style anime illustration,
clean cel-shading, crisp line art, detailed character design,
official art quality, high resolution

IMPORTANT: pure solid white background, completely empty background,
no scenery, no environment, no props, isolated character

no text, no watermark, no signature, no logo
```

### A.2 背景 Prompt 模板

```
COMPLETELY DESERTED SCENE, ZERO PEOPLE, ZERO FIGURES,
ZERO SOLDIERS, ZERO GUARDS

Wide cinematic establishing shot, scenic environment view, 16:9 aspect

location category: ancient Chinese palace hall
architectural structures only, all inhabitants absent

detailed scene: grand palace interior with red lacquered columns,
golden dragon throne, elaborate wooden carvings,
tall bronze incense burners, silk curtains

lighting and atmosphere: deep blue moonlit sky through lattice windows,
warm glow from bronze oil lamps, mysterious shadows

masterpiece anime visual novel background art,
galgame style scenic background, Makoto Shinkai inspired,
ultra detailed environment, cinematic composition

pure environment landscape, architectural and natural elements only,
completely empty scene, uninhabited

(EMPTY ENVIRONMENT ONLY — absolutely no soldiers, no guards, no warriors,
no human figures, no silhouettes, no NPCs)

no text, no watermark
```

---

## 附录 B：错误码定义

| 错误码 | 说明 | 处理建议 |
|---|---|---|
| `IMG001` | API Key 无效 | 检查环境变量 |
| `IMG002` | API 调用超时 | 重试，增加超时时间 |
| `IMG003` | 图片尺寸不合规 | 自动修正尺寸 |
| `IMG004` | 内容审核拒绝 | 修改 prompt |
| `IMG005` | 背景去除失败 | 检查图片格式 |
| `IMG006` | 存储上传失败 | 检查存储配置 |

---

**文档版本**：v1.0
**最后更新**：2026-05-23
**适用项目**：xuxie_agent

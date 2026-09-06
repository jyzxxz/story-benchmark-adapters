# Stage_Keyframe_Identity_AR_Blueprint

> **权威 AR 蓝图**。本文件是优化 cron 的唯一真理来源。每项 `[ ]` 只能在对应的代码改动 + 测试 + 验收证据都到位时被改成 `[_]`(worker 自测)/`[x]`(master 集成)。本蓝图**不得用 Prompt 文案改动蒙混过关** —— 必须真正打通"角色结构化身份 → 身份母版立绘 → 关键帧角色绑定 → 参考图输入 → 身份验收 → 重试 → 安全合成兜底"七步链路。
>
> **设计哲学**: 关键帧人物不再由"角色名 + appearance 文本"独立文生图,而是先确定每个角色的身份契约,基于已通过立绘验收的**身份母版**作为参考图输入,调用支持多图参考的图像编辑模型生成,生成后做**人物身份验收**(脸/发型/年龄/性别/服装/位置),失败时按"参考图重试 → 原立绘合成兜底"顺序降级,**禁止**默认走纯文字降级。

---

## 源范围(Source Scope)

| 关注点 | 当前位置 | 目标位置 |
|---|---|---|
| 关键帧角色提取 | `backend/app/services/prompt_builder_service.py::_extract_characters_for_keyframe` / `_find_character_appearance` | 改为返回 `KeyframeCharacterBinding`,不再退化为 `{name, appearance}` |
| 关键帧 prompt 构建 | `backend/app/services/prompt_builder_service.py::build_keyframe_prompts` / `build_keyframe_prompts_async` | 接收完整 binding,产出带 `CHARACTER IDENTITY LOCK` 的最终 prompt |
| Asset 编排 | `backend/app/services/asset_management_service.py::_build_keyframe_prompts_from_moments` / `generate_chapter_keyframes` | 先解析身份母版,缺则补生;调用 `generate_keyframe_with_references` |
| 关键帧生成 | `backend/app/services/image_generation_service.py::generate_keyframe` / `generate_image` | 新增 `generate_keyframe_with_references`(多图输入),独立模型配置 |
| Prompt Rewriter | `backend/app/services/prompt_rewriter_service.py` | 关键帧 rewriter 不得修改脸/发/性别/年龄/身份,只能改场景/动作/构图/光线 |
| 关键帧时刻选择 | `backend/app/services/keyframe_moment_selector.py` | 每个角色输出 `emotion / outfit / pose / action / injury_state / held_item` |
| VNGraph 编排 | `backend/app/services/vn_graph_assembler.py::_auto_generate_missing` | 改为两阶段调度:**portraits before keyframes** |
| 模型 | `backend/app/models.py` | 关键帧 Asset `generation_params` schema 升级到 `keyframe-identity-v2` |

环境变量(新增):
- `KEYFRAME_REFERENCE_MODE` = `edit`(默认)
- `KEYFRAME_IMAGE_MODEL` = 支持多图输入的已配置图像编辑模型
- `KEYFRAME_REFERENCE_MAX_IMAGES` = `3`
- `KEYFRAME_IDENTITY_MAX_RETRIES` = `2`
- `KEYFRAME_ALLOW_TEXT_ONLY_FALLBACK` = `false`(默认)
- `CHARACTER_IDENTITY_CONTRACT_VERSION` = `character-identity-v2`

---

## 十九、必须修复关键帧人物与立绘身份不一致

这是高优先级阻断问题。

当前关键帧中的人物只是根据角色名和一段简化外貌描述重新进行纯文字生图,并没有真正继承已经生成的角色立绘,因此关键帧中的人物会出现:

1. 脸型与立绘不同;
2. 发型、发色与立绘不同;
3. 年龄感和体型发生变化;
4. 性别表现发生变化;
5. 标志性服装和饰品丢失;
6. 多角色场景出现人物身份互换;
7. 同一个角色在不同关键帧中再次换脸。

不得只在 Prompt 中追加"same character"或"保持一致"。必须完成角色身份契约、参考图条件生成、身份验收和失败兜底。

### 1. 先检查当前信息丢失位置

重点检查:

```text
backend/app/services/character_visual_profile_service.py
backend/app/services/prompt_builder_service.py
backend/app/services/prompt_rewriter_service.py
backend/app/services/asset_management_service.py
backend/app/services/image_generation_service.py
backend/app/services/keyframe_moment_selector.py
backend/app/services/vn_graph_assembler.py
backend/app/models.py
```

当前需要重点确认并修复:

```text
PromptBuilderService._extract_characters_for_keyframe
PromptBuilderService._find_character_appearance
PromptBuilderService.build_keyframe_prompts
PromptBuilderService.build_keyframe_prompts_async
AssetManagementService._build_keyframe_prompts_from_moments
AssetManagementService.generate_chapter_keyframes
ImageGenerationService.generate_keyframe
ImageGenerationService.generate_image
VNGraphAssembler._auto_generate_missing
```

不得继续让关键帧角色数据退化为:

```python
{
    "name": character_name,
    "appearance": appearance_text
}
```

### 2. 建立角色身份契约

新增通用数据结构,例如:

```python
@dataclass(frozen=True)
class CharacterIdentityContract:
    version: str
    project_id: int
    character_id: str
    character_name: str

    gender: str
    visual_profile: dict[str, Any]
    visual_fingerprint: str
    visual_prompt_en: str
    identity_anchor: str

    face_shape: str
    skin_tone: str
    eye_color: str
    hair_color: str
    hair_length: str
    hair_style: str
    age_group: str
    body_build: str

    canonical_outfit: dict[str, Any]
    signature_features: tuple[str, ...]
    accessories: tuple[str, ...]

    master_portrait_asset_id: int | None
    reference_image_url: str | None
    reference_image_sha256: str | None
```

建议版本:

```text
CHARACTER_IDENTITY_CONTRACT_VERSION=character-identity-v2
```

身份契约必须由已经存在的:

```text
visual_profile
visual_prompt_en
visual_fingerprint
character_visual_anchor
gender_prompt
```

确定性生成。

不得让关键帧链路再独立推断角色外貌。

### 3. 每个角色建立身份母版立绘

每个角色必须有一张身份母版:

```text
emotion = neutral
outfit = default
pose = standing
```

选择规则:

1. `character_id` 必须匹配;
2. `visual_fingerprint` 必须匹配;
3. `style_fingerprint` 必须匹配;
4. 优先 neutral/default/standing;
5. 必须是 completed 且通过立绘验收的资产;
6. 不能使用旧版、stale、failed 或 quarantined 资产;
7. 找不到时,必须先生成身份母版,再生成关键帧。

在立绘 `generation_params` 中保存:

```json
{
  "identity_contract_version": "character-identity-v2",
  "is_identity_master": true,
  "visual_fingerprint": "...",
  "style_fingerprint": "...",
  "reference_image_sha256": "...",
  "identity_validation": {}
}
```

其他情绪立绘不得成为新的独立人物身份,只是身份母版的变体。

### 4. 身份参考图不得被透明背景处理破坏

当前立绘去背景可能直接覆盖生成文件。

请为身份参考保留独立图像:

```text
原始生图
→ identity reference
→ 去背景
→ presentation portrait
```

至少保存:

```text
source_image_url
identity_reference_url
presentation_url
```

如果不保留原始图,可把标准化透明立绘合成到中性纯色背景上,生成专用的:

```text
identity_reference_url
```

身份参考图要求:

* 人脸清楚;
* 发型完整;
* 上半身或全身完整;
* 无其他人物;
* 无复杂背景;
* 无文字;
* 不得使用严重侧脸或遮挡脸部的图片。

### 5. 关键帧角色绑定必须携带完整身份数据

新增:

```python
@dataclass(frozen=True)
class KeyframeCharacterBinding:
    character_id: str
    character_name: str
    visual_fingerprint: str
    identity_contract_version: str
    identity_prompt: str
    gender_prompt: str

    requested_emotion: str
    requested_outfit: str
    requested_pose: str
    requested_action: str

    frame_role: str
    expected_position: str

    reference_asset_id: int
    reference_image_url: str
    reference_image_sha256: str
```

`_extract_characters_for_keyframe()` 和 `_build_keyframe_prompts_from_moments()` 必须返回完整 binding。

不得只返回角色名和 appearance。

关键帧 Prompt Rewriter 输入至少包含:

```json
{
  "character_id": "...",
  "name": "...",
  "visual_profile": {},
  "visual_fingerprint": "...",
  "identity_anchor": "...",
  "gender_prompt": "...",
  "signature_features": [],
  "canonical_outfit": {},
  "requested_emotion": "...",
  "requested_outfit": "...",
  "requested_pose": "...",
  "requested_action": "...",
  "expected_position": "left|center|right",
  "reference_image_index": 1
}
```

### 6. Prompt Rewriter 不得重新设计人物

关键帧 Prompt Rewriter 只能改写:

```text
场景
动作
构图
镜头
表情
光线
人物位置
```

禁止修改:

```text
脸型
发型
发色
瞳色
年龄
性别
体型
标志性饰品
基础服装设计
角色身份
```

最终 Prompt 中的人物身份块必须由后端确定性组装,不能交给 LLM 自由生成。

例如:

```text
CHARACTER IDENTITY LOCK

Image 1 is 林夜.
character_id: ...
visual_fingerprint: ...
Preserve exactly:
- same facial identity
- same face shape
- same hairstyle and hair color
- same apparent age
- same gender presentation
- same body build
- same signature accessories
- same canonical outfit design

Allowed changes:
- facial expression
- body pose
- viewing angle
- action
- minor cloth movement caused by the action

Forbidden:
- redesigning the character
- changing hairstyle
- changing face
- changing age
- changing gender
- changing signature outfit
- swapping identity with another character
```

多角色时,每个角色必须分别声明:

```text
Image 1 = 角色A,位于左侧
Image 2 = 角色B,位于右侧
Image 3 = 角色C,位于中间或后景
```

明确禁止:

```text
identity swap
face blending
merged characters
one reference character replacing another
```

### 7. 接入参考图条件生成

新增独立的关键帧参考图生成入口,例如:

```python
async def generate_keyframe_with_references(
    *,
    event_name: str,
    scene_description: str,
    character_bindings: list[KeyframeCharacterBinding],
    action: str,
    emotion: str,
    final_prompt: str,
    style_fingerprint: str,
    seed: int,
) -> dict[str, Any]:
    ...
```

增加配置:

```text
KEYFRAME_REFERENCE_MODE=edit
KEYFRAME_IMAGE_MODEL=<支持多图输入的已配置图像编辑模型>
KEYFRAME_REFERENCE_MAX_IMAGES=3
KEYFRAME_IDENTITY_MAX_RETRIES=2
```

不得直接修改全局普通文生图模型配置,关键帧参考图模型单独配置。

调用图像编辑接口前,要核对当前所用地域、API Key、Workspace Endpoint 和模型是否支持多图输入。

请求结构采用:

```python
content = [
    {"image": reference_image_1},
    {"image": reference_image_2},
    {"text": final_prompt},
]
```

或按照当前官方接口要求的等效形式。

本地静态图片如果外部服务无法访问,必须转换为:

```text
data:image/png;base64,...
```

不得把只有本机才能访问的:

```text
/static/assets/...
localhost
127.0.0.1
```

直接传给外部图像服务。

最多传入三张人物参考图;当前关键帧也应继续限制最多三个主要角色。

### 8. 调整生成依赖顺序

当前 VNGraph 自动生成模式可能并行启动立绘和关键帧。

必须改成有依赖的两阶段调度:

```text
阶段 A:
身份母版立绘生成、验收并持久化

阶段 B:
背景、关键帧、配音可以按合理依赖并发
```

至少保证:

```python
await generate_or_resolve_identity_master_portraits(project_id)

await asyncio.gather(
    generate_backgrounds(...),
    generate_keyframes_with_references(...),
    generate_voices(...),
)
```

关键帧开始生成前必须重新查询数据库,确认所有预期角色都有可用的身份母版。

找不到参考立绘时:

* 不允许静默退回纯文字关键帧;
* 默认应记录 `missing_identity_reference`;
* 可先补生成身份母版;
* 补生成仍失败时,关键帧阶段失败或进入安全合成兜底。

只有显式配置:

```text
KEYFRAME_ALLOW_TEXT_ONLY_FALLBACK=true
```

时才允许纯文字降级,并且必须在结果中标记:

```text
identity_fallback_used = true
```

默认应为 false。

### 9. 为关键帧增加稳定 seed

新增:

```python
def keyframe_identity_seed(
    project_id: int,
    chapter_index: int,
    event_name: str,
    style_fingerprint: str,
    character_visual_fingerprints: list[str],
) -> int:
    ...
```

seed 输入至少包括:

```text
project_id
chapter_index
event_name 或 moment fingerprint
style_fingerprint
按 character_id 排序后的 visual_fingerprint
```

将 seed 真正传给:

```text
generate_keyframe
generate_image
Qwen parameters
```

不得继续无 seed 生成关键帧。

seed 只能辅助稳定性,不能替代参考图。

### 10. 关键帧生成后必须做人物身份验收

新增:

```text
backend/app/services/keyframe_identity_validator_service.py
```

建议输出:

```python
@dataclass
class KeyframeCharacterValidation:
    character_id: str
    expected_name: str
    reference_asset_id: int

    detected: bool
    matched_face_index: int | None
    face_similarity: float | None
    visual_similarity: float | None

    gender_match: bool | None
    age_match: bool | None
    hair_match: bool | None
    outfit_match: bool | None
    signature_feature_match: bool | None

    passed: bool
    reasons: list[str]
```

整体输出:

```python
@dataclass
class KeyframeIdentityValidationResult:
    expected_character_count: int
    detected_character_count: int
    unexpected_character_count: int
    identity_swap_detected: bool
    character_results: list[KeyframeCharacterValidation]
    passed: bool
    reasons: list[str]
```

验收至少包括:

1. 预期人物数量;
2. 额外人物数量;
3. 人脸相似度;
4. 发型和发色;
5. 年龄与性别表现;
6. 标志性饰品;
7. 主要服装颜色与轮廓;
8. 多角色是否发生身份互换;
9. 左、中、右位置是否与 binding 一致。

可采用:

```text
本地人脸检测 + 人脸 embedding
本地 CLIP / DINO 类视觉 embedding
结构化 VLM 兜底
```

测试时必须全部 mock,不得真实调用付费视觉模型。

不能因为动作、角度或表情变化,就要求像素级完全相同。

### 11. 身份验收失败时重试

第一次失败后,分析具体原因:

```text
face_mismatch
hair_mismatch
outfit_mismatch
identity_swap
missing_character
unexpected_character
gender_mismatch
age_mismatch
```

根据原因构造针对性 retry Prompt。

例如:

```text
The previous output changed Lin Ye's facial identity.
Regenerate using Image 1 as the mandatory identity reference.
Keep exactly the same face shape, hairstyle, hair color,
apparent age, gender presentation and signature clothing.
Change only the pose and expression.
```

多角色身份互换:

```text
Do not swap the identities.
Image 1 must remain the left character.
Image 2 must remain the right character.
Their faces, hair and clothing must not be blended.
```

重试必须:

* 使用相同参考图;
* 使用确定性 retry seed;
* 保持场景和剧情内容不变;
* 最多重试配置次数;
* 每轮保存验证报告。

### 12. 增加严格身份安全兜底

如果参考图生成连续失败,不得把错脸图片保存为 completed。

新增:

```text
render_mode = generated_reference
render_mode = sprite_composite
render_mode = text_only_degraded
```

默认兜底顺序:

```text
参考图条件生成
→ 身份验收
→ 带参考图重试
→ 使用已验收背景 + 已验收透明立绘进行合成
```

`sprite_composite` 可以牺牲部分动态动作,但必须保证:

* 人物就是原立绘;
* 人物身份不会变化;
* 位置、缩放和遮挡关系合理;
* 整体调色与当前场景一致;
* 不生成错误的人物面孔。

纯文字生成只能作为显式开启的最低级降级,不得默认使用。

### 13. 保存完整关键帧身份元数据

关键帧 Asset 的 `generation_params` 至少保存:

```json
{
  "schema_version": "keyframe-identity-v2",
  "render_mode": "generated_reference",
  "model": "...",
  "seed": 123,
  "style_fingerprint": "...",
  "identity_contract_version": "character-identity-v2",
  "character_bindings": [
    {
      "character_id": "...",
      "character_name": "...",
      "visual_fingerprint": "...",
      "reference_asset_id": 1,
      "reference_image_sha256": "...",
      "requested_emotion": "...",
      "requested_outfit": "...",
      "requested_pose": "...",
      "expected_position": "left"
    }
  ],
  "reference_image_count": 1,
  "identity_validation": {},
  "identity_retry_count": 0,
  "identity_fallback_used": false,
  "final_prompt": "..."
}
```

不得继续只保存:

```text
style_fingerprint
visual_style_profile
visual_style_prompt
final_prompt
```

### 14. 缓存和失效

关键帧缓存 key 必须包括:

```text
keyframe identity schema version
image model
final prompt
seed
style fingerprint
全部 character_id
全部 visual_fingerprint
全部 reference_asset_id
全部 reference_image_sha256
requested outfit
requested emotion
requested pose
```

以下任一变化时,旧关键帧必须 stale:

1. StoryBible 中角色 visual_profile 改变;
2. visual_fingerprint 改变;
3. 身份母版立绘重新生成;
4. reference image hash 改变;
5. 项目视觉风格 fingerprint 改变;
6. 关键帧模型改变;
7. identity validator 版本改变。

不得只比较项目 `style_fingerprint`。

### 15. 防止服装状态不一致

关键帧不仅要保持脸一致,还要保持剧情时刻的服装状态。

KeyframeMomentSelector 或下游分析必须为每个角色输出:

```text
emotion
outfit
pose
action
injury state
held item
```

选择参考立绘时:

1. 优先身份母版保证人物身份;
2. 若有对应 outfit 变体,可作为额外或替代参考;
3. 不得因为关键帧需要战斗动作,就重新设计整套服装;
4. 只允许剧情明确要求的服装变化;
5. 当前章节未提到换装时,必须使用 canonical outfit。

### 16. 测试

新增:

```text
backend/tests/test_character_identity_contract.py
backend/tests/test_keyframe_reference_resolver.py
backend/tests/test_keyframe_reference_generation.py
backend/tests/test_keyframe_identity_validator.py
backend/tests/test_keyframe_identity_retry.py
backend/tests/test_keyframe_sprite_composite_fallback.py
backend/tests/test_keyframe_asset_dependency_order.py
```

至少覆盖:

1. 关键帧角色数据包含 visual_profile;
2. 包含 visual_fingerprint;
3. 包含 identity anchor;
4. 包含 reference asset ID;
5. 普通关键帧请求携带一至三张参考图;
6. 本地 URL 自动转 Base64;
7. 超过三名角色只选择主要三人;
8. 同一个角色多张关键帧使用同一身份母版;
9. 身份母版变化后旧关键帧失效;
10. 立绘未完成时关键帧不会提前启动;
11. 并行资产模式下仍保证 portraits before keyframes;
12. 人脸明显不一致时验收失败;
13. 人物身份互换时验收失败;
14. 发型、性别、年龄明显变化时验收失败;
15. 验收失败后携带原参考图重试;
16. 达到重试上限后进入 sprite composite;
17. 错误关键帧不会进入 completed manifest;
18. 测试不调用真实付费 API。

使用本地生成的测试图或 fixture:

```text
同一人物不同表情
同一人物不同姿态
明显不同人物
发型变化人物
性别变化人物
双人位置互换
```

### 17. 验收标准

整改完成后必须满足:

1. 关键帧不再只依赖角色名和 appearance 文本;
2. 每个角色有唯一身份母版;
3. 关键帧生成前身份母版必须存在;
4. 关键帧请求真正携带人物参考图;
5. 同一人物的 visual_fingerprint 在立绘和关键帧中一致;
6. 关键帧生成使用稳定 seed;
7. 关键帧生成后执行身份验收;
8. 多角色场景能检测身份互换;
9. 不合格关键帧不会保存为 completed;
10. 连续失败时使用原立绘合成兜底;
11. 人物母版更新后相关关键帧自动失效;
12. 普通新故事和三国公开故事都能兼容;
13. 不真实调用付费 API 运行测试。

### 18. 完成报告必须增加

完成后报告:

1. 原关键帧人物换脸的代码根因;
2. 立绘到关键帧丢失了哪些身份字段;
3. CharacterIdentityContract 完整结构;
4. 身份母版选择规则;
5. 关键帧参考图请求结构;
6. 本地图片如何转换为可发送的图片数据;
7. portraits before keyframes 的依赖修改;
8. 一个单人关键帧完整 Prompt;
9. 一个双人关键帧完整 Prompt;
10. 两个 Prompt 对应的 reference binding;
11. 身份验收指标和阈值;
12. 身份互换检测方式;
13. 重试策略;
14. sprite composite 兜底方式;
15. 关键帧 generation_params 示例;
16. 缓存失效策略;
17. 全部测试命令和结果;
18. `git status --short`;
19. `git diff --check`;
20. 确认新增文件均已被 git 跟踪。

不要只修改 Prompt 文案。必须真正打通:

```text
角色结构化身份
→ 身份母版立绘
→ 关键帧角色绑定
→ 参考图输入
→ 身份验收
→ 重试
→ 安全合成兜底
```

---

## Execution Checklist

> 本 section 是 execution cron 的权威 checklist。每项格式:`- [ ] **<id>** <slug> — <description>`。
> checkbox 状态语义:
> - `[ ]` 未完成 / 未被 worker 认领
> - `[_]` worker 已自测通过,等 master 集成
> - `[x]` master 已集成并通过验收
>
> **section 分层**:Section A(契约/母版) → B(关键帧绑定) → C(参考图生成) → D(身份验收+重试+兜底) → E(调度+缓存+服装) → F(测试)。**严禁跨层前置勾选**。
>
> 依赖关系写在每项末尾 `Depends on:` 行。Owned paths 在 `Paths:` 行。

### Section A — 角色身份契约 + 身份母版立绘(worker A owns)

- [_] **A01** character-identity-contract-dataclass — 新增 `CharacterIdentityContract` frozen dataclass,字段完整(version / project_id / character_id / character_name / gender / visual_profile / visual_fingerprint / visual_prompt_en / identity_anchor / face_shape / skin_tone / eye_color / hair_color / hair_length / hair_style / age_group / body_build / canonical_outfit / signature_features / accessories / master_portrait_asset_id / reference_image_url / reference_image_sha256)。落到 `backend/app/services/character_identity_contract.py`。
  Paths: `backend/app/services/character_identity_contract.py`
  Depends on: (none)

- [_] **A02** identity-contract-builder-from-visual-profile — 实现 `build_identity_contract(project_id, character_id, visual_profile, visual_fingerprint, visual_prompt_en, identity_anchor, gender_prompt) -> CharacterIdentityContract`,**确定性**生成,所有字段从已有 visual_profile / visual_prompt_en / visual_fingerprint / character_visual_anchor / gender_prompt 派生。落到 `character_identity_contract.py::build_identity_contract`。
  Paths: `backend/app/services/character_identity_contract.py`
  Depends on: A01

- [_] **A03** identity-contract-version-config — 在 `backend/app/core/config.py` 新增常量 `CHARACTER_IDENTITY_CONTRACT_VERSION = "character-identity-v2"`;支持环境变量覆盖。落到 `backend/app/core/config.py`。
  Paths: `backend/app/core/config.py`
  Depends on: A01

- [_] **A04** identity-master-portrait-selection-rule — 实现 `resolve_identity_master_portrait(project_id, character_id, visual_fingerprint, style_fingerprint) -> Asset | None`,按蓝图第 3 节 7 条选择规则:character_id 匹配 → visual_fingerprint 匹配 → style_fingerprint 匹配 → 优先 neutral/default/standing → 必须 completed 且通过立绘验收 → 排除 stale/failed/quarantined。落到 `backend/app/services/identity_master_resolver.py`。
  Paths: `backend/app/services/identity_master_resolver.py`
  Depends on: A01, A02

- [_] **A05** identity-master-portrait-generation — 实现 `generate_or_resolve_identity_master_portraits(project_id, ...) -> dict[character_id, Asset]`,找不到母版时调用现有立绘生成链路补生,立绘 `generation_params` 写入 `{"identity_contract_version": "character-identity-v2", "is_identity_master": true, "visual_fingerprint": ..., "style_fingerprint": ..., "reference_image_sha256": ..., "identity_validation": {}}`。落到 `identity_master_resolver.py`。
  Paths: `backend/app/services/identity_master_resolver.py`
  Depends on: A04

- [_] **A06** identity-reference-image-preservation — 修复"立绘去背景覆盖原文件"问题。立绘 Asset `generation_params` 至少保存 `source_image_url` / `identity_reference_url` / `presentation_url` 三条路径。若原文件已丢失,把透明 PNG 合成到中性纯色背景生成 `identity_reference_url`。落到 `image_generation_service.py` 的立绘生成路径 + `models.py` 字段补充。
  Paths: `backend/app/services/image_generation_service.py`, `backend/app/models.py`
  Depends on: A05

- [_] **A07** identity-reference-quality-guard — 身份参考图质量校验:人脸清楚 / 发型完整 / 上半身或全身完整 / 无其他人物 / 无复杂背景 / 无文字 / 非严重侧脸或遮挡。失败时拒绝作为身份参考,触发补生。落到 `identity_master_resolver.py::_validate_identity_reference_quality`。
  Paths: `backend/app/services/identity_master_resolver.py`
  Depends on: A06

### Section B — 关键帧角色绑定(worker B owns)

- [_] **B01** keyframe-character-binding-dataclass — 新增 `KeyframeCharacterBinding` frozen dataclass,字段:character_id / character_name / visual_fingerprint / identity_contract_version / identity_prompt / gender_prompt / requested_emotion / requested_outfit / requested_pose / requested_action / frame_role / expected_position / reference_asset_id / reference_image_url / reference_image_sha256。落到 `backend/app/services/keyframe_character_binding.py`。
  Paths: `backend/app/services/keyframe_character_binding.py`
  Depends on: A01

- [_] **B02** extract-characters-for-keyframe-rewrite — 改写 `PromptBuilderService._extract_characters_for_keyframe` 和 `_find_character_appearance`,**返回 `list[KeyframeCharacterBinding]`**,**禁止**再返回 `{"name": ..., "appearance": ...}` 退化结构。落点到 `prompt_builder_service.py`。
  Paths: `backend/app/services/prompt_builder_service.py`
  Depends on: B01, A04

- [_] **B03** keyframe-prompt-builder-with-identity-lock — 改写 `PromptBuilderService.build_keyframe_prompts` / `build_keyframe_prompts_async`,接收 `list[KeyframeCharacterBinding]`,产出含 `CHARACTER IDENTITY LOCK` 块的最终 prompt(单人 + 多人变体),多角色必须分别声明 `Image N = 角色 X,位于 left|center|right`,显式 forbidden identity swap/face blending。落点到 `prompt_builder_service.py`。
  Paths: `backend/app/services/prompt_builder_service.py`
  Depends on: B02

- [_] **B04** asset-mgr-build-keyframe-from-moments — 改写 `AssetManagementService._build_keyframe_prompts_from_moments`,返回完整 binding,不再只传 name/appearance。落点到 `asset_management_service.py`。
  Paths: `backend/app/services/asset_management_service.py`
  Depends on: B01, B02

- [_] **B05** keyframe-prompt-rewriter-input-schema — 关键帧 Prompt Rewriter 输入 schema 升级,至少包含 `character_id / name / visual_profile / visual_fingerprint / identity_anchor / gender_prompt / signature_features / canonical_outfit / requested_emotion / requested_outfit / requested_pose / requested_action / expected_position / reference_image_index`。落点到 `prompt_rewriter_service.py`。
  Paths: `backend/app/services/prompt_rewriter_service.py`
  Depends on: B01

- [_] **B06** keyframe-prompt-rewriter-scope-lock — 关键帧 rewriter 只允许改写:场景/动作/构图/镜头/表情/光线/人物位置;**禁止**修改:脸型/发型/发色/瞳色/年龄/性别/体型/标志性饰品/基础服装设计/角色身份。后置校验 `_has_identity_drift(rewritten_prompt, original_identity_block)` 检测漂移,漂移则回退到原始 identity block。落到 `prompt_rewriter_service.py`。
  Paths: `backend/app/services/prompt_rewriter_service.py`
  Depends on: B05

### Section C — 参考图条件生成(worker C owns)

- [_] **C01** keyframe-image-model-config — 在 `backend/app/core/config.py` 新增配置项 `KEYFRAME_REFERENCE_MODE` / `KEYFRAME_IMAGE_MODEL` / `KEYFRAME_REFERENCE_MAX_IMAGES=3` / `KEYFRAME_IDENTITY_MAX_RETRIES=2` / `KEYFRAME_ALLOW_TEXT_ONLY_FALLBACK=false`,独立于全局普通文生图模型。落到 `config.py`。
  Paths: `backend/app/core/config.py`
  Depends on: (none)

- [_] **C02** multi-image-input-capability-check — 调用图像编辑接口前核对:当前地域 / API Key / Workspace Endpoint / `KEYFRAME_IMAGE_MODEL` 是否支持多图输入。不支持时拒绝调用并报错(不得静默回退到无参考图)。落到 `image_generation_service.py::_verify_multi_image_support`。
  Paths: `backend/app/services/image_generation_service.py`
  Depends on: C01

- [_] **C03** local-image-to-data-url — 实现 `to_sendable_image_data(image_url) -> str`:本地 `/static/assets/...`、`localhost`、`127.0.0.1` URL 必须读取后转 `data:image/png;base64,...`;已经是对外可访问 URL 则原样返回。**禁止**把本机 only 路径直接传外部服务。落到 `image_generation_service.py::_to_sendable_image_data`。
  Paths: `backend/app/services/image_generation_service.py`
  Depends on: (none)

- [_] **C04** keyframe-reference-generation-entry — 新增 `ImageGenerationService.generate_keyframe_with_references(event_name, scene_description, character_bindings, action, emotion, final_prompt, style_fingerprint, seed) -> dict`,按蓝图第 7 节请求结构 `content = [{"image": ...}, ..., {"text": final_prompt}]` 调用。**最多三张人物参考图**,超过三名角色时选择 frame_role 优先级最高的三人。落到 `image_generation_service.py`。
  Paths: `backend/app/services/image_generation_service.py`
  Depends on: C01, C02, C03, B01

- [_] **C05** keyframe-identity-seed — 实现 `keyframe_identity_seed(project_id, chapter_index, event_name, style_fingerprint, character_visual_fingerprints) -> int`,seed 输入至少含 `project_id / chapter_index / event_name 或 moment fingerprint / style_fingerprint / 按 character_id 排序的 visual_fingerprint`。seed 真正传给 `generate_keyframe / generate_image / Qwen parameters`。落到 `keyframe_character_binding.py` 或新 `keyframe_identity_seed.py`。
  Paths: `backend/app/services/keyframe_character_binding.py`
  Depends on: (none)

- [_] **C06** asset-mgr-generate-chapter-keyframes-rewrite — 改写 `AssetManagementService.generate_chapter_keyframes`,先解析身份母版(缺则补生 A05),再调用 `generate_keyframe_with_references`,关键帧 `generation_params` 写入 `keyframe-identity-v2` schema(含 `schema_version / render_mode / model / seed / style_fingerprint / identity_contract_version / character_bindings[] / reference_image_count / identity_validation / identity_retry_count / identity_fallback_used / final_prompt`)。落点到 `asset_management_service.py`。
  Paths: `backend/app/services/asset_management_service.py`, `backend/app/models.py`
  Depends on: C04, A05

### Section D — 身份验收 + 重试 + 安全合成兜底(worker D owns)

- [_] **D01** keyframe-identity-validator-service-skeleton — 新增 `backend/app/services/keyframe_identity_validator_service.py`,定义 `KeyframeCharacterValidation` 和 `KeyframeIdentityValidationResult` dataclass,字段与蓝图第 10 节完全一致。落到 `keyframe_identity_validator_service.py`。
  Paths: `backend/app/services/keyframe_identity_validator_service.py`
  Depends on: (none)

- [_] **D02** validator-9-dimension-checks — 验收至少含 9 项:(1) 预期人物数量 (2) 额外人物数量 (3) 人脸相似度 (4) 发型发色 (5) 年龄性别表现 (6) 标志性饰品 (7) 服装颜色与轮廓 (8) 多角色身份互换检测 (9) 左中右位置与 binding 一致性。实现路径:本地人脸检测 + embedding / CLIP 或 DINO 视觉 embedding / 结构化 VLM 兜底。落到 `keyframe_identity_validator_service.py`。
  Paths: `backend/app/services/keyframe_identity_validator_service.py`
  Depends on: D01

- [_] **D03** validator-identity-swap-detection — 多角色场景身份互换检测:按 expected_position 与人脸 embedding 比对,任一角色 face embedding 命中错误角色或与参考图不匹配则 `identity_swap_detected = true`。落到 `keyframe_identity_validator_service.py::_detect_identity_swap`。
  Paths: `backend/app/services/keyframe_identity_validator_service.py`
  Depends on: D02

- [_] **D04** validator-all-mock-in-tests — 测试中所有 VLM/CLIP/face-embedding 调用必须 mock,**禁止**真实调用付费视觉模型。提供 `_StubValidator` 测试桩。落到 `keyframe_identity_validator_service.py`。
  Paths: `backend/app/services/keyframe_identity_validator_service.py`
  Depends on: D02

- [_] **D05** retry-on-identity-failure — 第一次验收失败后,按失败原因(`face_mismatch / hair_mismatch / outfit_mismatch / identity_swap / missing_character / unexpected_character / gender_mismatch / age_mismatch`)构造针对性 retry prompt。重试约束:相同参考图 + 确定性 retry seed + 场景剧情不变 + 最多 `KEYFRAME_IDENTITY_MAX_RETRIES` 次 + 每轮保存验证报告。落到 `asset_management_service.py` 或新 `keyframe_retry.py`。
  Paths: `backend/app/services/asset_management_service.py`
  Depends on: D02, C04

- [_] **D06** sprite-composite-fallback — 连续失败时使用 `sprite_composite`:已验收背景 + 已验收透明立绘合成。保证人物就是原立绘 / 身份不变 / 位置缩放遮挡合理 / 整体调色一致 / 不生成错误面孔。`render_mode = sprite_composite`。落到新 `keyframe_sprite_composer.py`。
  Paths: `backend/app/services/keyframe_sprite_composer.py`
  Depends on: D05

- [_] **D07** no-completed-on-validation-fail — 验收失败的关键帧**不得**保存为 completed,必须保持 failed/quarantined 状态。错误关键帧不会进入 completed manifest。落到 `asset_management_service.py`。
  Paths: `backend/app/services/asset_management_service.py`
  Depends on: D05, D06

- [_] **D08** text-only-fallback-explicit-flag — 纯文字降级只在 `KEYFRAME_ALLOW_TEXT_ONLY_FALLBACK=true`(默认 false)时启用,结果标记 `identity_fallback_used = true`,`render_mode = text_only_degraded`。落到 `asset_management_service.py`。
  Paths: `backend/app/services/asset_management_service.py`
  Depends on: D06

### Section E — 调度 + 缓存 + 服装状态(worker E owns)

- [_] **E01** portraits-before-keyframes-scheduling — 改写 `VNGraphAssembler._auto_generate_missing`,改两阶段调度:`await generate_or_resolve_identity_master_portraits(project_id)` 完成后再 `await asyncio.gather(generate_backgrounds(...), generate_keyframes_with_references(...), generate_voices(...))`。关键帧开始前重新查 DB 确认所有角色都有可用身份母版。落点到 `vn_graph_assembler.py`。
  Paths: `backend/app/services/vn_graph_assembler.py`
  Depends on: A05, C04

- [_] **E02** missing-identity-reference-not-silent — 找不到参考立绘时**禁止**静默回退纯文字;默认记录 `missing_identity_reference`,先补生身份母版;补生失败时关键帧阶段失败或进 sprite composite 兜底。落到 `vn_graph_assembler.py` / `asset_management_service.py`。
  Paths: `backend/app/services/vn_graph_assembler.py`, `backend/app/services/asset_management_service.py`
  Depends on: E01

- [_] **E03** keyframe-cache-key-v2 — 关键帧缓存 key 必须含 `keyframe identity schema version / image model / final prompt / seed / style fingerprint / 全部 character_id / 全部 visual_fingerprint / 全部 reference_asset_id / 全部 reference_image_sha256 / requested outfit / requested emotion / requested pose`。落到 `image_generation_service.py::_get_cache_key` 的 keyframe 分支或新 `keyframe_cache_key.py`。
  Paths: `backend/app/services/image_generation_service.py`
  Depends on: C06

- [_] **E04** keyframe-stale-on-7-changes — 以下任一变化时旧关键帧 stale:(1) StoryBible 角色 visual_profile 改变 (2) visual_fingerprint 改变 (3) 身份母版重新生成 (4) reference image hash 改变 (5) 项目风格 fingerprint 改变 (6) 关键帧模型改变 (7) identity validator 版本改变。落到 `asset_management_service.py::_is_keyframe_stale`。
  Paths: `backend/app/services/asset_management_service.py`
  Depends on: E03

- [_] **E05** keyframe-moment-selector-clothing-state — `KeyframeMomentSelector`(或下游分析)为每个角色输出 `emotion / outfit / pose / action / injury_state / held_item`。选择参考立绘时:优先身份母版 → 有 outfit 变体可作额外参考 → 不得因战斗动作重设整套服装 → 只允许剧情明确换装 → 未提到换装时用 canonical outfit。落到 `keyframe_moment_selector.py`。
  Paths: `backend/app/services/keyframe_moment_selector.py`
  Depends on: B01

### Section F — 测试(worker F owns,全 mock,无付费 API)

- [_] **F01** test-character-identity-contract — 新增 `backend/tests/test_character_identity_contract.py`,覆盖 A01-A03:契约字段完整性、确定性生成、版本号配置。**不真实调用付费 API**。
  Paths: `backend/tests/test_character_identity_contract.py`
  Depends on: A01, A02, A03

- [_] **F02** test-keyframe-reference-resolver — 新增 `backend/tests/test_keyframe_reference_resolver.py`,覆盖:身份母版选择规则(character_id/vf/sf/neutral/completed/排除 stale)、参考图质量守卫、母版缺失时补生。
  Paths: `backend/tests/test_keyframe_reference_resolver.py`
  Depends on: A04, A05, A07

- [_] **F03** test-keyframe-reference-generation — 新增 `backend/tests/test_keyframe_reference_generation.py`,覆盖:1-3 张参考图请求结构、本地 URL 自动转 Base64、>3 角色选主要 3 人、多图输入 capability check、稳定 seed 计算。
  Paths: `backend/tests/test_keyframe_reference_generation.py`
  Depends on: C02, C03, C04, C05

- [_] **F04** test-keyframe-identity-validator — 新增 `backend/tests/test_keyframe_identity_validator.py`,覆盖:人脸明显不一致验收失败、身份互换检测、发型/性别/年龄明显变化验收失败、左中右位置 mismatch、9 维检查覆盖、所有 VLM/CLIP/face-embedding 全 mock。
  Paths: `backend/tests/test_keyframe_identity_validator.py`
  Depends on: D01, D02, D03, D04

- [_] **F05** test-keyframe-identity-retry — 新增 `backend/tests/test_keyframe_identity_retry.py`,覆盖:验收失败后携带原参考图重试、针对性 retry prompt、确定性 retry seed、达到重试上限后停止。
  Paths: `backend/tests/test_keyframe_identity_retry.py`
  Depends on: D05

- [_] **F06** test-keyframe-sprite-composite-fallback — 新增 `backend/tests/test_keyframe_sprite_composite_fallback.py`,覆盖:达到重试上限后进入 sprite composite、错误关键帧不进 completed manifest、纯文字降级仅在显式开启时启用并标记 fallback。
  Paths: `backend/tests/test_keyframe_sprite_composite_fallback.py`
  Depends on: D06, D07, D08

- [_] **F07** test-keyframe-asset-dependency-order — 新增 `backend/tests/test_keyframe_asset_dependency_order.py`,覆盖:立绘未完成时关键帧不启动、并行资产模式下仍 portraits before keyframes、同一角色多关键帧共用同一身份母版、身份母版变化后旧关键帧失效。
  Paths: `backend/tests/test_keyframe_asset_dependency_order.py`
  Depends on: E01, E04

- [_] **F08** test-fixtures-preparation
> **F 系列实现说明**：实际测试文件按主题命名而非蓝图条目顺序：
> - `test_character_identity_contract.py` (F01)
> - `test_keyframe_character_binding.py` (F02 binding 部分)
> - `test_keyframe_identity_validator_service.py` (F03/F04)
> - `test_keyframe_sprite_composer.py` (F06)
> - `test_keyframe_cache_v2.py` (F05 cache + E03/E04 stale)
> - `test_keyframe_moment_clothing_state.py` (E05)
> - `test_identity_master_resolver.py` (F02 resolver 部分)
> - `test_keyframe_identity_pipeline.py` (F05 retry + D07/D08)
> - F08 测试 fixture PNG 在测试中按需用 PIL 现生，无外部素材依赖。
 — 准备本地测试 fixture 图:同一人物不同表情、同一人物不同姿态、明显不同人物、发型变化人物、性别变化人物、双人位置互换。**禁止**用真实付费生成图。落到 `backend/tests/fixtures/keyframe_identity/`。
  Paths: `backend/tests/fixtures/keyframe_identity/`
  Depends on: (none)

---

## Completion Acceptance

蓝图勾选 `[x]` 前必须满足(对应第 17 节):

1. 关键帧不再只依赖角色名和 appearance 文本 —— B02/B03/B04 已 `[x]`
2. 每个角色有唯一身份母版 —— A04/A05 已 `[x]`
3. 关键帧生成前身份母版必须存在 —— E01/E02 已 `[x]`
4. 关键帧请求真正携带人物参考图 —— C04 已 `[x]`
5. 同一人物 visual_fingerprint 在立绘和关键帧中一致 —— B02 + C06 已 `[x]`
6. 关键帧生成使用稳定 seed —— C05 已 `[x]`
7. 关键帧生成后执行身份验收 —— D02 已 `[x]`
8. 多角色场景能检测身份互换 —— D03 已 `[x]`
9. 不合格关键帧不会保存为 completed —— D07 已 `[x]`
10. 连续失败时使用原立绘合成兜底 —— D06 已 `[x]`
11. 人物母版更新后相关关键帧自动失效 —— E04 已 `[x]`
12. 普通新故事和三国公开故事都能兼容 —— F01-F07 已 `[x]`,跑通现有 three-kingdoms chapter 测试
13. 不真实调用付费 API 运行测试 —— F01-F07 全部 mock 验证已 `[x]`

完成后报告(对应第 18 节)必须包含 20 项:代码根因 / 丢失字段清单 / 契约结构 / 母版选择规则 / 参考图请求结构 / 本地图转 base64 / 调度依赖修改 / 单人 prompt 样例 / 双人 prompt 样例 / reference binding 样例 / 验收指标阈值 / 身份互换检测 / 重试策略 / sprite composite 兜底 / generation_params 样例 / 缓存失效策略 / 全部测试命令与结果 / `git status --short` / `git diff --check` / 新增文件 git 跟踪确认。

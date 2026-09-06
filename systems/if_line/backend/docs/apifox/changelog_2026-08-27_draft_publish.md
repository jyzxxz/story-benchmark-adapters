# API-CHANGE：草稿区 + 物化锚定 + 固化发布（2026-08-27）

> 基线：上一次提交 `381e355 feat(agent): AutoCreator 桥接 v2 revisions`
> 结论：**全部为非 BREAKING 变更**，无数据库迁移；不传新参数时行为与旧版完全一致。
> Apifox 已同步（项目 8663103，覆盖式更新 + 新增接口/模型）。

## 一、新增接口（3 个）

### 1. POST /api/chapter-revisions/{chapter_revision_id}/script-revisions

手动创建章节脚本修订（**草稿物化**）。客户端编辑的脚本草稿固化为不可变修订。

- `Idempotency-Key` 必填
- Body `ScriptRevisionCreate`：`script_json`（完整 IR，必填）、`parent_revision_id`（可空）、`auto_register_characters`（默认 true）
- 201 → `ScriptRevisionCreated`（含 `appended_character_names`——**前端需按名合并进本地 bible 草稿**，否则发布固化会覆盖回写增量角色）
- 422：spans 与锚定正文不逐字一致（手改 spans 必失败，合法途径=重新生成后采用）
- 语义：内容寻址去重幂等（同内容重复提交返回既有修订）；**不激活 head**

### 2. POST /api/chapter-script-revisions/{script_revision_id}/vn-graph-revisions

手动创建 VNGraph 修订（草稿物化）。

- Body `VNGraphRevisionCreate`：`graph_json`（必填）、`parent_revision_id`（可空）
- 201 → `VNGraphRevision`
- 409：该脚本已存在编译绑定且草稿内容与绑定图不一致（改图走 VNGraph patch）
- 422：`validate_detailed` 校验失败
- 语义：同上，去重幂等、不激活 head

### 3. POST /api/projects/{project_id}/finalize-publish

**固化发布**：把物化后的草稿修订 id 集作为锚点，单事务内按链序激活（bible→outline→章节→脚本→VNGraph，章节按 display_index 拓扑排序以 provisional 链前驱已审校验）并发布为同项目新 Release。

- `Idempotency-Key` 必填（贯穿发布重放：同 key 同请求返回同一 Release）
- Body `FinalizePublishRequest`：
  - `bible_revision_id?`、`outline_revision_ids?{story_path_id: id}`、`chapter_revision_ids?`、`script_revision_ids?`、`graph_revision_ids?`（键均为 path_chapter_id）
  - 只传**有草稿的 subject**；不传的类别沿用当前 head（等同直接 publish）
  - `release_notes?`（≤2000）
- 201 → `Release`（version +1）
- 422 `release.not_ready`：`details.blocking_items` 列出阻塞项（如 `script.resource_unbound` / `chapter.bible_mismatch`）→ 前端按此引导"重做下游"
- 409：`chapter_revision.provisional_ancestors_unreviewed`（前驱章未审，后端已按链序规避）/ `release.no_changes` / `release.content_already_released`

## 二、修改接口（body 新增可选字段，均为非 BREAKING）

| 接口 | 变化 |
|---|---|
| `POST /api/story-paths/{id}/outline-generations` | `OutlineGenerationRequest` 新增可选 `bible_revision_id`（草稿优先生成：先物化设定草稿再传其修订 id） |
| `POST /api/path-chapters/{id}/generations` | requestBody 引用新模型 `ChapterGenerationRequest`：可选 `bible_revision_id` + `outline_revision_id`（**必须成对**，resolver 契约）、可选 `ancestor_revision_overrides`（`{path_chapter_id: revision_id}`，前章草稿进续写上下文）、`instructions`/`parameters` 原有 |
| `POST /api/story-paths/{id}/chapter-generation-batches` | `ChapterBatchRequest` 新增可选 `bible_revision_id` + `outline_revision_id`（成对；批量内部按 provisional 链自管前章） |

## 三、新增数据模型（5 个）

`FinalizePublishRequest`、`ScriptRevisionCreate`、`ScriptRevisionCreated`、`VNGraphRevisionCreate`、`ChapterGenerationRequest`

## 四、前端适配要点

1. **生成带草稿的流程**：有上游草稿时**先物化**（调 createRevision 系列）→ 拿 revision_id → 作为锚点传给生成接口。
2. **发布流**：逐类物化草稿（幂等，可放心重试）→ 收集 anchors → `finalize-publish`。422 的 `blocking_items` 直接映射到"哪类草稿需要重做下游"。
3. **脚本能手改的只有演出字段**（emotion / keyframe_prompt / keyframe_required）；spans 手改必 422。
4. **回写合并**：脚本生成/手动物化的 `appended_character_names` 要合并进本地 bible 草稿。
5. 草稿本身在浏览器 localStorage（`if-line:draft:v1:*`），无服务端草稿 API。

## 五、同步说明

- 本地文件：`ifline_product.openapi.json`（增量合并，保留人工润色）+ `source/` 已重建 + `catalog.json` 已更新
- 同步命令：`backend/venv/bin/python backend/docs/apifox/sync_apifox_overwrite.py`（本次为 sync 脚本补充了"远端缺失即 create"分支，原只做覆盖更新）

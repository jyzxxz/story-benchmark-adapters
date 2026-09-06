# 【前端对接】草稿区 + 固化发布 接口变更（2026-08-27，全部非 BREAKING）

Apifox 已同步（搜索接口名即可）：`createScriptRevision` / `createVNGraphRevision` / `finalizeAndPublish`。
详细版见 `backend/docs/apifox/changelog_2026-08-27_draft_publish.md`。

## 新增 3 个接口

| 接口 | 用途 | 关键点 |
|---|---|---|
| `POST /api/chapter-revisions/{chapter_revision_id}/script-revisions` | 章节脚本草稿物化 | spans 必须与正文逐字一致（手改必 422）；返回 `appended_character_names` 要合并进 bible 草稿 |
| `POST /api/chapter-script-revisions/{script_revision_id}/vn-graph-revisions` | VNGraph 草稿物化 | 409=该脚本已有编译绑定且内容不一致（改图走 patch） |
| `POST /api/projects/{project_id}/finalize-publish` | 固化发布成书 | Idempotency-Key 必填；422 时 `details.blocking_items` = 需重做的下游清单 |

## 3 个生成接口新增可选锚点字段（不传=行为不变）

1. `outline-generations` body + `bible_revision_id?`
2. `path-chapters/{id}/generations` body 换模型 `ChapterGenerationRequest`：`bible_revision_id?` + `outline_revision_id?`（**必须成对**）+ `ancestor_revision_overrides?`（前章续写上下文）
3. `chapter-generation-batches` body + 成对 `bible_revision_id?` / `outline_revision_id?`

## 前端需要做的事

1. 有草稿时生成：先调对应 createRevision 物化 → 拿 id 当锚点 → 再调生成。
2. 发布：逐类物化（幂等可重试）→ 组 anchors → finalize-publish；422 的 blocking_items 定位到具体草稿引导重做。
3. 脚本草稿编辑只开放演出字段（emotion/keyframe_*），spans 只读。
4. 生成/物化返回的 `appended_character_names` 按 name 合并进本地 bible 草稿。

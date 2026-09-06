# 图像资源生成与版本链路

本文描述 2026-08-02 起生效的视觉资源生产契约。项目章节的 AI 图像只允许从不可变的 Chapter Script revision 发起，不再提供项目级、全书级或本章三类资源一键生成。

## 唯一生产链路

```text
ChapterRevision
  -> ChapterScriptRevision
  -> ScriptResourceSlot
  -> AssetPlan / AssetPlanItem
  -> GenerationTask (asset.render.batch -> asset.render)
  -> Logical Asset
  -> AssetVersion
  -> StorageObject
  -> ScriptResourceSlot + AssetBinding
  -> VNGraphRevision
  -> ProjectRelease
```

`portrait`、`background`、`keyframe` 必须分别触发。一个请求只能包含一个 `role`，不提供项目级、全书级或三类合并生成。

## HTTP 接口

图像服务探活：

```http
GET /api/image-generation/status
```

响应只包含 `enabled` 与 `model`，不暴露服务端目录。

从精确 Chapter revision 获取当前 Script Head，再读取该 Script revision 的资源槽：

```http
GET /api/chapter-revisions/{chapter_revision_id}/script-head
GET /api/chapter-script-revisions/{script_revision_id}/resource-slots
```

按类型创建资源任务：

```http
POST /api/chapter-script-revisions/{script_revision_id}/resource-renders
Content-Type: application/json

{"role":"portrait"}
```

`role` 只允许 `portrait`、`background`、`keyframe`。

为当前 Script revision 的 Slot 选择已有版本：

```http
PUT /api/chapter-script-revisions/{script_revision_id}/resource-slots/{slot_id}
Content-Type: application/json

{"asset_version_id":"..."}
```

项目全局资源库：

```http
GET /api/projects/{project_id}/assets
GET /api/projects/{project_id}/assets/{asset_id}
GET /api/projects/{project_id}/assets/{asset_id}/versions
```

`GET /assets` 按 Logical Asset 分页，`total` 是逻辑资源数。当前页的每个资源嵌套全部 `AssetVersion`，版本按 `version_no DESC` 排序。每个版本只通过 `media_url` 返回受权限控制的 `/api/media/{storage_object_id}` 地址，不暴露 `storage_object_id`、`storage_key`、本地路径或 provider 临时路径。

## 逻辑资源与版本

Logical Asset 是项目级语义身份，AssetVersion 是不可变表现结果。

稳定逻辑 key 不包含 revision ID：

```text
portrait:{character_id}:{emotion}:{outfit}:{pose}
background:{scene_semantic_id}:{time}:{weather}:{visual_state}
keyframe:chapter-revision-{chapter_revision_id}:{source_span_or_event_key}
```

规则如下：

- 语义身份与完整渲染规格均相同：复用已有 AssetVersion。
- 语义身份相同、渲染规格变化：在同一 Logical Asset 下创建新版本。
- 语义身份变化：创建新的 Logical Asset。
- `latest_version_id` 只用于资源库默认展示，不能驱动 VN Graph 或 Release。
- Script Slot、AssetBinding、VN Graph 和 Release 始终固定具体 `asset_version_id`。

示例：

```text
Script R1 -> portrait A / version 1
Script R2 (same spec) -> reuse version 1
Script R2 (changed render spec) -> portrait A / version 2
Script R1 remains pinned to version 1
```

## 计划与任务

Chapter Script 创建时分配 `ScriptResourceSlot`，并把冻结的资源与渲染规格写入 `AssetPlan` / `AssetPlanItem`。完整计划不再追加到 `Asset.generation_params`。

分类请求创建零成本父任务 `asset.render.batch`，每个待生成 Logical Asset 创建一个 `asset.render` 子任务。父子关系由 `GenerationTaskDependency` 表示。

没有该 role 的 Slot 时，系统仍返回一个立即成功的 no-op 父任务。重复请求相同 Script revision、role 和规格时复用同一任务或缓存版本，不重复生成和扣费。

父任务允许 `partial`：

- Slot 在子任务排队后进入 `generating`。
- 子任务最终失败后对应 Slot 进入 `failed`。
- 重试 partial 父任务时，只重试依赖表中失败且仍可重试的子任务。
- 子任务重试时 Slot 回到 `generating`。
- 成功后 Slot 与 AssetBinding 固定新版本并进入 `bound`。

## 类型渲染器

`asset.render` worker 按 `asset_type` 分派：

```text
PortraitTaskRenderer
BackgroundTaskRenderer
KeyframeTaskRenderer
```

worker 只读取任务中冻结的 `asset_spec` 与 `render_spec`。provider、model、prompt、negative prompt、seed、尺寸、风格、身份、后处理与校验版本在排队时确定，执行时不能重新读取“当前”设定。

只有 `asset.render` worker 可以为项目章节生成并写入新的 AI 图像 AssetVersion。上传和图库选用是非生成来源；作者侧 AssetAction 不接受 `direct_generate`，也不允许 Agent 低匹配自动转生图。

## 存储事务

文件使用随机不可变路径：

```text
generated-portrait/YYYY/MM/{uuid}.png
generated-background/YYYY/MM/{uuid}.png
generated-keyframe/YYYY/MM/{uuid}.png
```

写入顺序：

```text
temporary file -> fsync -> atomic rename
-> StorageObject -> AssetVersion -> Slot/Binding -> database commit
```

数据库持久化失败时回滚记录并删除刚写入的文件。StorageObject 只在所有数据库引用都消失后进入 GC；GC 物理删除前重新检查 AssetVersion、LibraryAsset、VoiceProfile 与 Release manifest。

Logical Asset 删除是归档。项目删除先软删除项目媒体，再通过数据库级联清理项目数据；`StorageObject.project_id` 使用 `SET NULL` 保留墓碑，最后由 GC 删除文件。

## VN Graph 与 Release

当前 Script revision 的全部 required Slot 都 bound 后，系统只创建一个幂等 `vngraph.compile` 任务。三类资源可按任意顺序生成；不存在需求的类型不阻塞编译。

VN Graph 输入固定 Chapter hash、Script hash、Slot 与 AssetVersion。生成新版本不会重编旧 Script revision。手动切换版本只更新目标 Script revision 的 Slot/Binding，并产生新的 VN Graph revision。

Release 从当前 StoryPathChapter、ChapterScriptHead 和 VNGraphHead 读取一致快照，并在 manifest 中固定 `path_chapter_id`、Revision UUID、`asset_version_id` 与 `storage_object_id`。已发布 Release 不因作者侧新增版本、分支同序号章节或切换当前 head 而漂移。

## 已删除入口

以下旧 POST 不再注册路由：

```text
/api/image-generation/{project_id}/portraits/generate
/api/image-generation/{project_id}/backgrounds/generate
/api/image-generation/{project_id}/backgrounds/generate-chapter/{chapter_index}
/api/image-generation/{project_id}/keyframes/generate
/api/image-generation/{project_id}/keyframes/generate-chapter/{chapter_index}
/api/image-generation/{project_id}/generate-all
```

前端旧 `imageGenerationApi.ts`、兼容 `vnGraphApi.ts`、旧 VisualAssetWorkbench 和章节 `generate-assets` 调用均已删除。

## 验收门禁

```bash
rg "portraits/generate|backgrounds/generate|keyframes/generate|generate-all" frontend/src backend/app
rg "imageGenerationApi|generateAllChapter|getAllAssets" frontend/src
rg "full-vn-graph/assemble" frontend/src
```

以上结果必须为空。OpenAPI 必须只保留图像服务 status，并包含分类 `resource-renders` 与嵌套 AssetVersion 契约。

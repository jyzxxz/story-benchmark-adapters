# 当前 API 问题清单

生成依据：当前分支 `create_app()` 实际注册的路由。当前运行时没有完全重叠的 `method/path` 路由。

## 总览

- 当前注册 HTTP handler：135 个。
- 完全重叠路由：0 个。
- 当前仍需处理：旧素材自动生成入口、维护/调试边界入口。

## 1. 旧素材自动生成入口仍在暴露

当前仍暴露旧 `image-generation` 自动生成类入口。`GET /api/image-generation/status` 仍作为图像服务探活入口保留，不列入本清单。这里主要问题是旧生成入口仍是同步或项目级编排语义，和当前任务化素材、章节脚本资源槽、视觉小说图编译链路没有完全合并。

| 旧接口 | 当前代码位置 | 推荐方向 | 判断 |
| --- | --- | --- | --- |
| `POST /api/image-generation/{project_id}/portraits/generate` | `backend/app/routers/image_generation.py:98` | 项目级素材编排入口，或章节脚本资源槽渲染任务 | 当前没有完全等价的新入口。旧接口是项目级角色立绘生成。 |
| `POST /api/image-generation/{project_id}/backgrounds/generate` | `backend/app/routers/image_generation.py:139` | 项目级素材编排入口，或章节脚本资源槽渲染任务 | 当前没有完全等价的新入口。旧接口会遍历项目章节并只生成背景。 |
| `POST /api/image-generation/{project_id}/backgrounds/generate-chapter/{chapter_index}` | `backend/app/routers/image_generation.py:190` | `POST /api/projects/{project_id}/chapters/{chapter_index}/chapter-scripts/revisions/{script_revision_id}/resource-renders` | 章节脚本资源槽可以承接“按脚本资源需求渲染背景”，但不是旧接口这种“只生成本章背景”的直接替换。 |
| `POST /api/image-generation/{project_id}/keyframes/generate` | `backend/app/routers/image_generation.py:219` | 项目级素材编排入口，或章节脚本资源槽渲染任务 | 当前没有完全等价的新入口。旧接口会遍历项目章节并只生成关键帧。 |
| `POST /api/image-generation/{project_id}/keyframes/generate-chapter/{chapter_index}` | `backend/app/routers/image_generation.py:268` | `POST /api/projects/{project_id}/chapters/{chapter_index}/chapter-scripts/revisions/{script_revision_id}/resource-renders` | 章节脚本资源槽可以承接“按脚本资源需求渲染关键帧”，但不是旧接口这种“只生成本章关键帧”的直接替换。 |
| `POST /api/image-generation/{project_id}/generate-all` | `backend/app/routers/image_generation.py:295` | 项目级/全书级素材编排入口 | 没有单接口替代。旧接口是一键生成全项目素材。 |

要清掉这一章，需要先决定是否保留项目级/全书级素材编排，以及是否需要按立绘、背景、关键帧分别生成。新的入口应采用任务系统，并优先和章节脚本资源槽、资源渲染任务对齐。

## 2. 维护和调试边界问题

| 接口 | 当前代码位置 | 问题 | 建议 |
| --- | --- | --- | --- |
| `/api/server-agent/*` | `backend/app/routers/server_agent.py:64` | 当前路由没有用户身份、项目归属或权限依赖，正式对外时边界不清晰。 | 可以暂时放内部或调试集合；进入产品 Apifox 前需要补用户/项目权限语义。 |

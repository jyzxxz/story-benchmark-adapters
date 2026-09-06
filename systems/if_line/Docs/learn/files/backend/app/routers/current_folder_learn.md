# backend/app/routers/ — folder learn

> FastAPI 路由层。3 个在 subset。

## 文件角色
- `image_generation.py` —— 图像 API（立绘/背景/关键帧/批量），P1.4 改造入口。
- `vn_graph.py` —— VNGraph API（按章），P3.3 加整章接口。
- `tts.py` —— TTS 单条 API，P2.2 扩章节批量（或新建 `voice.py`）。

## ⭐ 通用路由模式（所有新路由照抄）
1. Pydantic `BaseModel` + `Field(default, description=...)`，空 body 也能调。
2. 开头 check `*_ENABLED`，未启用 raise `HTTPException(503)`。
3. `xlog.info/warn/error(project_id, ...)` 日志（不是标准 logging）。
4. 批量返回 `{total, generated, failed, results}` 结构。
5. 单条查询 `db.query(Model).filter(...).first()`，不存在 raise 404。
6. 导出用 `JSONResponse(content=..., headers={"Content-Disposition": ...})`。

## 路由注册
`backend/app/main.py` 用 `app.include_router(...)`。P2 新建 `voice.py` 时记得注册：
```python
from app.routers import voice
app.include_router(voice.router, prefix="/api/tts", tags=["voice"])
```

## 不在 subset 但相关
- `chapters.py` —— 章节正文 API，P2 切片会读这里的 ChapterContent。
- `auto_agent.py` —— auto_creator 触发入口，跑全链路时可能要加 voice/vngraph 回调。
- `assets.py` / `projects.py` / `workflow.py` —— 项目/工作流 API。

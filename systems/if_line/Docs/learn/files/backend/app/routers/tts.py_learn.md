# routers/tts.py — learn note

> Source: `backend/app/routers/tts.py` (74 LOC)
> Route: `standard` | Reuse target: **P2.2 章节 batch 接口可扩这里或新建 voice.py**
> Status: `[_]` → master-validated `[x]`

## 职责
单条 TTS 合成接口 + 状态查询。

## 现有接口
- `POST /api/tts/synthesize` → 单条合成（文本 → audio_url）
- `GET /api/tts/status` → `{enabled, engine}`

## P2.2 改造：加章节批量接口

**推荐新建** `backend/app/routers/voice.py`（职责更清晰），不挤进 `tts.py`：

```python
# voice.py
@router.post("/chapters/{project_id}/{chapter_index}/generate-batch")
async def generate_chapter_voices(project_id, chapter_index, db):
    if not TTS_ENABLED:
        raise HTTPException(503, "TTS 功能未启用")
    from app.services.chapter_voice_service import chapter_voice_service
    result = await chapter_voice_service.generate_chapter_voices(project_id, chapter_index, db)
    return result  # {total, generated, failed, voice_lines, chapter_duration_sec}

@router.get("/chapters/{project_id}/{chapter_index}/manifest")
async def get_chapter_manifest(project_id, chapter_index, db):
    """返回 VoiceLine[]，前端播放器初始化用"""
    assets = db.query(Asset).filter(
        Asset.project_id == project_id,
        Asset.chapter_index == chapter_index,
        Asset.asset_type == "voice_line",
    ).order_by(Asset.id).all()
    return [_asset_to_voice_line(a) for a in assets]
```

## ⭐ Request/Response 模式（直接抄 tts.py）
- Pydantic `BaseModel` 带 `Field(..., description=...)`。
- `xlog.info/warn` 日志。
- 503 检查 `TTS_ENABLED`。

## ⭐ 注册路由
`backend/app/main.py` 加：
```python
from app.routers import voice
app.include_router(voice.router, prefix="/api/tts", tags=["voice"])
```
prefix 用 `/api/tts` 保持和现有 `tts.py` 一致。

## worker 提示
- manifest 接口要 `order_by(Asset.id)` 保证播放顺序。
- voice_line 落库时按章节正文顺序，不能并发落库乱序（合成可并发，落库要保序）。

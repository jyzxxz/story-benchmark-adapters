# routers/image_generation.py — learn note

> Source: `backend/app/routers/image_generation.py` (428 LOC)
> Route: `standard` | Reuse target: **P1.4 加 auto_demand 参数**
> Status: `[_]` → master-validated `[x]`

## 职责
图像生成 API：立绘 / 背景 / 关键帧 / 批量。

## P1.4 改造点

### `PortraitGenerateRequest` (L34-37)
加字段：
```python
class PortraitGenerateRequest(BaseModel):
    auto_demand: bool = Field(True, description="内容驱动：从章节正文自动决定情绪/装束/姿态变体")
    variations: Optional[List[PortraitVariation]] = Field(None, description="(deprecated) 手动指定变体；auto_demand=True 时忽略")
    batch_size: int = Field(5)
```

### `generate_portraits` handler (L96-126)
加 `auto_demand` 透传：
```python
result = await service.generate_all_portraits(
    project_id=project_id,
    variations=variations,
    batch_size=request.batch_size,
    auto_demand=request.auto_demand,
)
```

### `generate_all_assets` handler (L348-375)
一键生成直接传 `auto_demand=True`。

## ⭐ 通用模式（P2 voice router 参考）
- 每个路由开头 check `IMAGE_GENERATION_ENABLED`，未启用 raise 503。
- `xlog.info/warn/error` 统一日志。
- 批量操作返回 `{total, generated, failed, results}` 结构 —— P2 voice batch 接口照抄。

## worker 提示
- 所有 Pydantic Request 都给默认值（`= PortraitGenerateRequest()`），保持现有「空 body 也能调」的接口兼容。
- P1.4 改完用 `curl -X POST /image-generation/<id>/portraits/generate -d '{}'` 测空 body 默认走 auto_demand=True。

# image_generation_service.py — learn note

> Source: `backend/app/services/image_generation_service.py` (77201 bytes, 估计 1800+ LOC)
> Route: `high_reasoning` | Reuse target: **P1 继续作为底层生成入口，不重写**
> Status: `[_]` → master-validated `[x]`

## 职责
封装 AI 画图后端（GLM/SDXL/Redux），暴露 `generate_portrait` / `generate_background_with_validation` / `generate_keyframe` 等方法。

## ⭐ 复用件：`generate_background_with_validation(spec, forbidden_characters, genre)`

P1 背景图链路（已是 v2 内容驱动）调用入口。返回 `gen_result` 含 `status / retry_count / fallback_type / image_path / reason`。

P1 立绘/关键帧 **不动** 这个 service 的内部实现，只通过 `asset_management_service` 的入口加 `auto_demand` 参数。

## ⭐ 并发模式
`asset_management_service.generate_all_portraits` (L65) 用 `batch_size` 切批串行 + 批内并发。**P2 chapter_voice_service 照抄这个并发模式**：

```python
for i in range(0, len(items), batch_size):
    batch = items[i:i+batch_size]
    # batch 内并发
```

## 状态/启用
- `IMAGE_GENERATION_ENABLED`、`AI_IMAGE_MODEL` 在文件顶部 export。所有路由开头 check enabled，未启用 503。

## worker 提示
- 这个文件很大（77KB），不要通读，按需 grep 方法名。
- P1 改造只在 `asset_management_service.py` 层加 analyzer，**不改这里**。
- 新加的 `voice_line` asset_type 不需要走 `image_generation_service`（音频走 `tts_service`），但 Asset 表落库逻辑可参考这里的 stat_service 调用模式。

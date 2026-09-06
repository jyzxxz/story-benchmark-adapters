# E06 — background-asset-variation-info

## 背景

新链路产出的 `BackgroundSceneSpec` 含 18 个字段，但数据库 `Asset` 表 schema 不能动（哲学要求）。需要把全字段塞进 `Asset.variation_info` JSON 字段——这是已有字段（用于存储 portrait 的 emotion/outfit 变体等）。

需要保证：
1. 新字段不冲突已有字段
2. JSON 序列化可逆（Pydantic model_dump_json / parse）
3. 旧 asset（无 variation_info 或字段缺失）能正常读取

## SOTA 实践

**JSON Schema evolution**（[`json-schema.org/understanding-json-schema/best-practices.html`](https://json-schema.org/understanding-json-schema/best-practices.html)）：JSON 字段演进应保持向后兼容，新字段可选。

**Pydantic JSON serialization**（[`docs.pydantic.dev/latest/concepts/serialization/`](https://docs.pydantic.dev/latest/concepts/serialization/)）：`model_dump_json` + `model_validate_json` 可逆序列化。

**Postgres JSONB best practices**（[`www.postgresql.org/docs/current/datatype-json.html`](https://www.postgresql.org/docs/current/datatype-json.html)）：JSONB 支持 GIN 索引，可高效查询嵌套字段。

## 落地建议

`asset_management_service.py::_persist_background_asset`：

```python
async def _persist_background_asset(
    self,
    project_id: int,
    chapter_index: int,
    spec: BackgroundSceneSpec,
    gen_result: GenerationResult,
    override_existing: bool,
    is_fallback: bool = False,
) -> Asset:
    # 旧 asset 处理
    if override_existing:
        await self._delete_existing_background(project_id, chapter_index, spec.scene_selector)

    # variation_info 序列化
    variation_info = {
        # === 新字段（background AR 链路） ===
        "schema_version": "bg_ar_v1",
        "scene_id": spec.scene_id,
        "scene_name": spec.scene_name,
        "scene_selector": spec.scene_selector,
        "scene_type": spec.scene_type,
        "split_reason": spec.split_reason,
        "evidence_spans": spec.evidence_spans,
        "environment_description": spec.environment_description,
        "architecture": spec.architecture,
        "props": spec.props,
        "lighting": spec.lighting,
        "weather": spec.weather,
        "time_of_day": spec.time_of_day,
        "atmosphere": spec.atmosphere,
        "camera_shot_type": spec.camera_shot_type,
        "composition_constraints": spec.composition_constraints,
        "people_policy_mode": spec.people_policy.mode,
        "people_policy_rationale": spec.people_policy.rationale,
        "style_tags": [t.model_dump() for t in spec.style_tags],
        "forbidden_characters": spec.forbidden_characters,
        # === 生成元数据 ===
        "is_fallback": is_fallback,
        "retry_count": gen_result.retry_count,
        "escalation_applied": gen_result.escalation_applied,
        "generation_status": gen_result.status,
        # === 兼容字段（旧 vn_graph 读取） ===
        "scene_location": spec.scene_name,  # 兼容旧 location 匹配
    }

    asset = Asset(
        project_id=project_id,
        chapter_index=chapter_index,
        asset_type="background",
        scene_location=spec.scene_name,  # 保留旧字段
        file_path=gen_result.image_path,
        prompt_used=gen_result.prompt,
        variation_info=variation_info,
    )
    self._db.add(asset)
    await self._db.commit()
    return asset
```

**反序列化**（`llm_vn_graph_generator.py`）：

```python
def _read_scene_spec_from_asset(self, asset: Asset) -> Optional[BackgroundSceneSpec]:
    vi = asset.variation_info or {}
    if vi.get("schema_version") != "bg_ar_v1":
        return None  # 旧 asset，无完整 spec
    try:
        return BackgroundSceneSpec(
            scene_id=vi["scene_id"],
            scene_name=vi["scene_name"],
            scene_selector=vi["scene_selector"],
            scene_type=vi["scene_type"],
            split_reason=vi["split_reason"],
            evidence_spans=vi.get("evidence_spans", []),
            environment_description=vi["environment_description"],
            architecture=vi.get("architecture", []),
            props=vi.get("props", []),
            lighting=vi["lighting"],
            weather=vi["weather"],
            time_of_day=vi["time_of_day"],
            atmosphere=vi["atmosphere"],
            camera_shot_type=vi["camera_shot_type"],
            composition_constraints=vi.get("composition_constraints", []),
            people_policy=PeoplePolicy(
                mode=vi["people_policy_mode"],
                rationale=vi.get("people_policy_rationale", ""),
            ),
            style_tags=[StyleTag(**t) for t in vi.get("style_tags", [])],
            forbidden_characters=vi.get("forbidden_characters", []),
        )
    except (KeyError, ValidationError) as e:
        logger.warning("variation_info_invalid", extra={"asset_id": asset.id, "err": str(e)})
        return None
```

## 风险与权衡

1. **variation_info JSON 膨胀**：单 asset JSON 可能 1-2KB（含 environment_description）。权衡：DB JSONB 存储无 size 问题；查询时按需读字段。
2. **schema_version 升级时旧数据兼容**：升级 bg_ar_v2 时，旧 v1 数据需迁移。权衡：保留 v1 字段；v2 加新字段时同时填 v1 fallback 值；读取时按 schema_version 分支。

## 完成判据自检

- ✅ 围绕"variation_info 持久化 spec"，不跑题到 vn_graph 匹配（E05）。
- ✅ 引用 JSON Schema evolution、Pydantic JSON、Postgres JSONB。
- ✅ 落到 `asset_management_service.py::_persist_background_asset` 和 `llm_vn_graph_generator.py::_read_scene_spec_from_asset`。
- ✅ 符合哲学：用 JSON 扩展不动 DB schema，不引入新模型供应商。

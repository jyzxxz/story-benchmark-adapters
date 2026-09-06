# E01 — background-asset-mgmt-orchestration

## 背景

`AssetManagementService.generate_chapter_backgrounds` 是新链路的编排入口。当前它直接调 segmenter + builder + generator；新设计要改为 **5 步编排**：

```
1. analyzer → BackgroundSceneSpec[]
2. classifier → 给每个 spec 补 style_tags
3. assembler → 拼出英文 prompt
4. generator → 生成图（含 retry / fallback）
5. validator → 验收；通过则 persist 到 Asset 表
```

接口签名必须**最小破坏**——保留现有返回结构 `{total, generated, failed, results}`，否则前端 / API 路由会断。

## SOTA 实践

**Pipeline pattern**（[`refactoring.guru/design-patterns/pipeline`](https://refactoring.guru/design-patterns/chain-of-responsibility)）：把多步骤处理拆为独立 stage，每个 stage 输入上一 stage 输出。

**Martin Fowler "orchestration vs choreography"**（[`martinfowler.com/articles/orchestrationVsChoreography.html`](https://martinfowler.com/articles/orchestrationVsChoreography.html)）：服务编排推荐显式 orchestration——单点控制流程，便于调试和日志关联。

**Pydantic pipeline**（[`docs.pydantic.dev/latest/`](https://docs.pydantic.dev/latest/)）：每 stage 用 Pydantic model 定义输入输出，类型保证。

## 落地建议

`asset_management_service.py::generate_chapter_backgrounds` 改造：

```python
async def generate_chapter_backgrounds(
    self,
    project_id: int,
    chapter_index: int,
    override_existing: bool = False,
) -> dict:
    # 取数据
    project = await self._get_project(project_id)
    outline = await self._get_chapter_outline(project_id, chapter_index)
    content = await self._get_chapter_content(project_id, chapter_index)
    story_bible = await self._get_story_bible(project_id)
    genre = project.genre

    # === 新链路 5 步 ===

    # Step 1: analyzer
    specs = await self._analyzer.analyze_chapter(
        chapter_index, content, outline, story_bible, genre
    )

    # Step 2: dedup by fingerprint
    specs = self._dedup_scenes(specs)

    # Step 3: classifier（batch）
    style_map = await self._classifier.classify_many(specs, genre)
    for spec in specs:
        spec.style_tags = style_map.get(spec.scene_id, [])

    # Step 4: forbidden_characters 多源合并（见 E02）
    forbidden_chars = self._collect_forbidden_characters(outline, specs, story_bible, content)
    for spec in specs:
        spec.forbidden_characters = forbidden_chars

    # Step 5: 每个场景：assembler → generator → validator → persist
    results = []
    for spec in specs:
        try:
            prompt = self._assembler.assemble(spec, spec.style_tags, forbidden_chars)

            # generator + validator + retry（image_generation_service 内部完成）
            gen_result = await self._image_gen.generate_background_with_validation(
                spec, prompt,
            )

            if gen_result.status == "success":
                asset = await self._persist_background_asset(
                    project_id, chapter_index, spec, gen_result, override_existing
                )
                results.append({"scene_id": spec.scene_id, "status": "success", "asset_id": asset.id})
            elif gen_result.status == "fallback_used":
                asset = await self._persist_background_asset(
                    project_id, chapter_index, spec, gen_result, override_existing,
                    is_fallback=True
                )
                results.append({"scene_id": spec.scene_id, "status": "fallback", "asset_id": asset.id})
            else:  # failed
                results.append({"scene_id": spec.scene_id, "status": "failed", "reason": gen_result.reason})
        except Exception as e:
            logger.exception("background_gen_failed", extra={"scene_id": spec.scene_id})
            results.append({"scene_id": spec.scene_id, "status": "error", "reason": str(e)})

    return {
        "total": len(results),
        "generated": sum(1 for r in results if r["status"] in ("success", "fallback")),
        "failed": sum(1 for r in results if r["status"] in ("failed", "error")),
        "results": results,
    }
```

**接口兼容性**：
- 返回结构 `{total, generated, failed, results}` 不变
- API 路由签名不变
- 数据库 Asset 表 schema 不变（用 variation_info 扩展，见 E06）

## 风险与权衡

1. **5 步串行 vs 并行**：每个 spec 走完 5 步 ~10s，4 spec = 40s 串行。权衡：用 `asyncio.gather` 并发 4 spec；但 LLM 限流可能让并发反而慢，首版先串行。
2. **某 spec 失败阻塞整章**：权衡：每 spec try/except，单个失败不影响其他；最终结果中标记 failed。

## 完成判据自检

- ✅ 围绕"5 步编排"，不跑题到具体 analyzer / classifier 实现。
- ✅ 引用 Pipeline pattern、Martin Fowler orchestration、Pydantic pipeline。
- ✅ 落到 `asset_management_service.py::generate_chapter_backgrounds`。
- ✅ 符合哲学：最小破坏接口，不引入新模型供应商；保证背景无人主体。

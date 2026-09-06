# routers/vn_graph.py — learn note

> Source: `backend/app/routers/vn_graph.py` (222 LOC)
> Route: `standard` | Reuse target: **P3.3 加整章接口**
> Status: `[_]` → master-validated `[x]`

## 职责
VNGraph 生成（规则版 + LLM 版）+ 查询 + 导出。**全部按章存取**。

## 现有接口
- `POST /{project_id}/chapters/{chapter_index}/generate-vn-graph` → 规则版生成
- `POST .../generate-vn-graph-llm` → LLM 版生成
- `GET .../vn-graph` → 查询
- `GET .../vn-graph/export` → JSONResponse 下载

## P3.3 改造点：加三个新接口

```python
@router.get("/{project_id}/chapters/{chapter_index}/full-vn-graph")
async def get_full_vn_graph(project_id, chapter_index, db):
    """整章完整 vngraph（文+图+音已装配）"""
    from app.services.vn_graph_assembler import vn_graph_assembler
    return await vn_graph_assembler.assemble_full_chapter(project_id, chapter_index, db, generate_missing=False)

@router.post("/{project_id}/chapters/{chapter_index}/full-vn-graph/assemble")
async def assemble_full_vn_graph(project_id, chapter_index, db):
    """显式触发装配：先生成缺失的图/音，再回填"""
    from app.services.vn_graph_assembler import vn_graph_assembler
    return await vn_graph_assembler.assemble_full_chapter(project_id, chapter_index, db, generate_missing=True)

@router.get("/{project_id}/chapters/{chapter_index}/full-vn-graph/export")
async def export_full_vn_graph(project_id, chapter_index, db):
    """导出整章 vngraph JSON 文件"""
    # 同 get_full_vn_graph，但包 JSONResponse + Content-Disposition
```

## ⭐ 路由模式参考（P3.3 直接抄）
- 查 `VNGraph` 表用 `.filter(VNGraph.project_id==..., VNGraph.chapter_index==...).first()`。
- 不存在 raise 404。
- `JSONResponse(content=..., headers={"Content-Disposition": ...})` 做下载。

## worker 提示
- 整章装配可能慢（要回填 voice），用 `async def`。
- `generate_missing=True` 路径要调 P2 `chapter_voice_service` 和 P1 图生成，依赖 DAG（P1+P2 必须先 `[x]`）。

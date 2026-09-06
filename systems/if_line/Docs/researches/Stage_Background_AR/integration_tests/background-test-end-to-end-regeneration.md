# E12 — background-test-end-to-end-regeneration

## 背景

最终验证：通过真实 API 端点对已有项目重生背景，验证新链路端到端跑通。这是 integration test，覆盖：
- API 路由层
- AssetManagementService 编排
- analyzer / classifier / assembler / generator / validator
- DB persist
- vn_graph 匹配

mock CogView-4 调用（避免真出图花成本），但跑真 LLM 调用（analyzer + classifier）。

## SOTA 实践

**FastAPI TestClient**（[`fastapi.tiangolo.com/reference/testclient/`](https://fastapi.tiangolo.com/reference/testclient/)）：标准 API integration test 工具。

**pytest-asyncio + httpx**（[`github.com/encode/starlette`](https://github.com/encode/starlette)）：async API 测试组合。

**Test containers**（[`github.com/testcontainers/testcontainers-python`](https://github.com/testcontainers/testcontainers-python)）：用 docker 起真实 DB（postgres）保证 integration 真实性。

## 落地建议

`backend/tests/test_background_e2e.py`：

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

@pytest.fixture
def test_app(test_db):
    """启动 app，用测试 DB"""
    from app.main import app
    return TestClient(app)

@pytest.fixture
def seeded_project(test_db):
    """准备一个完整 project（outline + content + story_bible）"""
    project = Project(name="测试项目", genre="historical")
    test_db.add(project)
    test_db.commit()

    outline = ChapterOutline(
        project_id=project.id, chapter_index=1,
        scene="夜雨旧站台",
        summary="主角在废弃站台发现线索",
        characters=["林夜", "苏晚晴"],
    )
    test_db.add(outline)

    content = ChapterContent(
        project_id=project.id, chapter_index=1,
        text="夜幕降临，雨水顺着锈蚀的站牌流下..." * 20,  # 200 字+
    )
    test_db.add(content)

    bible = StoryBible(
        project_id=project.id,
        characters=[
            {"name": "林夜", "english_name": "Lin Ye", "aliases": ["阿夜"]},
            {"name": "苏晚晴", "english_name": "Su Wanqing", "aliases": []},
        ],
    )
    test_db.add(bible)
    test_db.commit()
    return project

@pytest.mark.asyncio
async def test_e2e_regenerate_backgrounds(test_app, seeded_project, mock_cogview, real_llm):
    """端到端：调 API 重生背景，验证完整链路"""

    # Act: 调 API
    response = test_app.post(
        f"/api/image-generation/{seeded_project.id}/backgrounds/generate-chapter/1"
    )

    # Assert: API 返回
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert data["generated"] >= 1
    assert data["failed"] == 0

    # Assert: DB 有 Asset 记录
    from app.models import Asset
    assets = await test_db.query(Asset).filter_by(
        project_id=seeded_project.id, chapter_index=1, asset_type="background"
    ).all()
    assert len(assets) >= 1

    # Assert: variation_info 含完整 BackgroundSceneSpec
    first_asset = assets[0]
    vi = first_asset.variation_info
    assert vi["schema_version"] == "bg_ar_v1"
    assert vi["scene_selector"]  # non-empty
    assert vi["scene_type"] in ["war_camp", "market", "bedroom", ...]
    assert vi["people_policy_mode"] in ["empty_required", "background_people_optional", "background_groups_required"]
    assert len(vi["style_tags"]) >= 3
    assert "林夜" in vi["forbidden_characters"]
    assert "Lin Ye" in vi["forbidden_characters"]  # 双版本

    # Assert: prompt 不含剧情
    prompt = first_asset.prompt_used
    for kw in ["发现", "到达", "赴约", "交易"]:
        assert kw not in prompt
    # named cast 段保留
    assert "Named cast exclusion" in prompt

    # Assert: vn_graph 能匹配到这个 asset
    from app.services.llm_vn_graph_generator import LLMVNGraphGenerator
    graph_gen = LLMVNGraphGenerator(test_db)
    matched = graph_gen._match_background_asset(
        scene_selector_query=vi["scene_selector"],
        candidates=assets,
    )
    assert matched is not None
    assert matched.id == first_asset.id


@pytest.fixture
def mock_cogview(monkeypatch):
    """mock CogView-4 调用，返回固定 image bytes"""
    async def fake_generate_image(prompt, **kwargs):
        # 返回简单 PNG（1x1 透明像素）
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
    monkeypatch.setattr("app.services.image_generation_service.generate_image", fake_generate_image)


@pytest.fixture
def real_llm():
    """真 LLM 调用（需要 SEGMENTER_API_KEY 环境变量）"""
    if not os.getenv("SEGMENTER_API_KEY"):
        pytest.skip("SEGMENTER_API_KEY not set")
    # 不 mock LLM，让真调用
```

**CI 中分两档跑**：
- 默认（无 API key）：跳过 e2e
- 有 API key（nightly build）：完整跑 e2e

## 风险与权衡

1. **真 LLM 调用让测试不稳定**：analyzer 偶发漂移可能让 e2e fail。权衡：用 mock LLM 也可（fake analyzer 返回固定 spec），但减弱真实度。
2. **测试 DB 准备复杂**：seeded_project fixture 字段多。权衡：用 factory_boy 或 model_bakery 简化数据构造。

## 完成判据自检

- ✅ 围绕"端到端测试"，不跑题到具体 service unit test。
- ✅ 引用 FastAPI TestClient、pytest-asyncio、Test containers。
- ✅ 落到 `backend/tests/test_background_e2e.py`。
- ✅ 符合哲学：端到端验证新链路完整跑通，不引入新模型供应商；最终守护背景图质量。

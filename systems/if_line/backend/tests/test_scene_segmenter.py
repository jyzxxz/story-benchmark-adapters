"""
Scene Segmenter 测试 + vn_graph 多背景匹配测试。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ---------- SceneSegmenterService fallback ----------

@pytest.mark.asyncio
async def test_segmenter_fallback_when_disabled(monkeypatch):
    """SCENE-SEG disabled 时降级到单段。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "false")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()
    segs = await svc.segment_chapter("任意内容", {"scene": "朝堂", "characters": ["李清照"]})
    assert len(segs) == 1
    assert segs[0].location == "朝堂"
    assert "李清照" in segs[0].characters_present


@pytest.mark.asyncio
async def test_segmenter_fallback_when_no_api_key(monkeypatch):
    """无 API key 时降级到单段。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "true")
    monkeypatch.setenv("SEGMENTER_API_KEY", "")
    monkeypatch.setenv("REWRITER_API_KEY", "")
    monkeypatch.setenv("AI_IMAGE_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()
    segs = await svc.segment_chapter("x" * 1000, {"scene": "朝堂", "characters": []})
    assert len(segs) == 1


@pytest.mark.asyncio
async def test_segmenter_fallback_on_short_content(monkeypatch):
    """章节正文太短 (<400 字) 时调 LLM 单段蒸馏（不再走无 LLM 的 _fallback_single）。
    2026-06-15: 正文短也调 LLM 分析场景，输出 environment_hint_en。
    """
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "true")
    monkeypatch.setenv("SEGMENTER_API_KEY", "fake-key")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)

    called = {"n": 0}

    async def fake_call_llm_single(self, content, outline):
        called["n"] += 1
        return mod.SceneSegment(
            segment_id=1, location="朝堂", location_en="imperial court",
            summary="", characters_present=[], mood="day",
            environment_hint_en="vast imperial court hall",
        )

    monkeypatch.setattr(mod.SceneSegmenterService, "_call_llm_single", fake_call_llm_single)
    svc = mod.SceneSegmenterService()
    segs = await svc.segment_chapter("短内容", {"scene": "朝堂"})
    assert len(segs) == 1
    # 必须调了 LLM 单段蒸馏（不再走无 LLM fallback）
    assert called["n"] == 1
    assert "imperial court" in segs[0].environment_hint_en


@pytest.mark.asyncio
async def test_segmenter_fallback_handles_none_mood(monkeypatch):
    """outline.emotion=None 时不应该崩，mood 兜底成 'day'。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "false")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()
    # emotion 显式 None（DB 里就是这种）
    segs = await svc.segment_chapter("x" * 1000, {"scene": "朝堂", "emotion": None})
    assert len(segs) == 1
    assert segs[0].mood == "day"


@pytest.mark.asyncio
async def test_segmenter_fallback_uses_summary_when_scene_missing(monkeypatch):
    """outline.scene=None + summary 非空 → fallback 用 summary 前 30 字，不能是 '未知场景'。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "false")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()
    summary = "林夜推开工作室的铁门时，里面传出一阵金属摩擦的刺耳声"
    segs = await svc.segment_chapter(
        "x" * 1000,
        {"scene": None, "summary": summary, "emotion": None},
    )
    assert len(segs) == 1
    # 不允许落到 "未知场景"
    assert segs[0].location != "未知场景"
    assert segs[0].location != ""
    # 应该是 summary 前缀
    assert summary[:20] in segs[0].location


@pytest.mark.asyncio
async def test_segmenter_fallback_default_when_both_scene_and_summary_missing(monkeypatch):
    """outline.scene=None + summary=None → 兜底到 'default scene'，不能是中文 '未知场景'。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "false")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()
    segs = await svc.segment_chapter("x" * 1000, {"scene": None, "summary": None})
    assert len(segs) == 1
    assert segs[0].location != "未知场景"
    # 落到 default scene / story scene 这种可画英文词
    assert segs[0].location in ("default scene", "story scene")
    segs = await svc.segment_chapter("短内容", {"scene": "朝堂"})
    assert len(segs) == 1


@pytest.mark.asyncio
async def test_segmenter_fallback_on_llm_exception(monkeypatch):
    """LLM 调用抛异常时降级到单段。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "true")
    monkeypatch.setenv("SEGMENTER_API_KEY", "fake-key")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()

    async def fake_call(*a, **kw):
        raise RuntimeError("network error")
    svc._call_llm = fake_call

    segs = await svc.segment_chapter("x" * 1000, {"scene": "朝堂", "characters": ["李"]})
    assert len(segs) == 1
    assert segs[0].location == "朝堂"


@pytest.mark.asyncio
async def test_segmenter_dedup_same_location(monkeypatch):
    """同地点的昼夜/事件变化是不同视觉时刻，不能按 location 合并。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "true")
    monkeypatch.setenv("SEGMENTER_API_KEY", "fake-key")
    import importlib
    from app.services import scene_segmenter_service as mod
    from app.services.scene_segmenter_service import SceneSegment
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()

    async def fake_call(content, outline):
        return [
            SceneSegment(
                segment_id=1,
                location="朝堂",
                mood="day",
                time_of_day="day",
                event_signature="群臣候朝",
                start_marker="清晨",
            ),
            SceneSegment(
                segment_id=2,
                location="朝堂",
                mood="night",
                time_of_day="night",
                event_signature="殿内烛火熄灭",
                start_marker="入夜",
            ),
            SceneSegment(segment_id=3, location="花园", mood="day"),
        ]
    svc._call_llm = fake_call

    segs = await svc.segment_chapter("x" * 2000, {"scene": "朝堂"})
    # 同一朝堂的白天与夜晚必须各自保留。
    locations = [s.location for s in segs]
    assert locations == ["朝堂", "朝堂", "花园"]
    assert len({s.scene_fingerprint for s in segs}) == 3


@pytest.mark.asyncio
async def test_segmenter_truncates_to_max(monkeypatch):
    """超过 MAX_SEGMENTS_PER_CHAPTER 的部分被截断。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "true")
    monkeypatch.setenv("SEGMENTER_API_KEY", "fake-key")
    import importlib
    from app.services import scene_segmenter_service as mod
    from app.services.scene_segmenter_service import SceneSegment
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()

    async def fake_call(content, outline):
        return [
            SceneSegment(segment_id=i, location=f"地点{i}", mood="day")
            for i in range(1, 8)
        ]
    svc._call_llm = fake_call

    segs = await svc.segment_chapter("x" * 2000, {"scene": "朝堂"})
    assert len(segs) <= mod.MAX_SEGMENTS_PER_CHAPTER


def test_segment_schema():
    """SceneSegment schema 校验。"""
    from app.services.scene_segmenter_service import SceneSegment
    s = SceneSegment(segment_id=1, location="朝堂", location_en="imperial hall", mood="day")
    assert s.location == "朝堂"
    assert s.location_en == "imperial hall"


def test_segment_schema_has_environment_hint_en_field():
    """ENV-DISTILL — SceneSegment 必须含 environment_hint_en 字段，默认空串。"""
    from app.services.scene_segmenter_service import SceneSegment
    s = SceneSegment(segment_id=1, location="工作室", mood="night")
    # 默认空
    assert hasattr(s, "environment_hint_en")
    assert s.environment_hint_en == ""
    # 显式赋值
    s2 = SceneSegment(
        segment_id=2,
        location="工作室",
        mood="night",
        environment_hint_en="a dim workbench lit by blue chip glow, window view of night street",
    )
    assert "workbench" in s2.environment_hint_en
    assert "blue chip glow" in s2.environment_hint_en


def test_segmenter_system_prompt_requires_environment_hint_en():
    """ENV-DISTILL — system prompt 必须含 environment_hint_en 关键词 + 禁人物语义说明。"""
    from app.services.scene_segmenter_service import SceneSegmenterService
    svc = SceneSegmenterService()
    sys_prompt = svc._build_system_prompt()
    sp_lower = sys_prompt.lower()
    # 字段名必须出现
    assert "environment_hint_en" in sys_prompt
    # 必须明确禁止人物/动作词
    assert "person" in sp_lower or "character" in sp_lower
    assert "no person" in sp_lower or "no human" in sp_lower \
        or "禁止" in sys_prompt or "forbidden" in sp_lower
    # 必须给出转译示例（含 environment_hint_en 内容）
    assert "workbench" in sp_lower or "workshop" in sp_lower \
        or "工作室" in sys_prompt or "桌" in sys_prompt


@pytest.mark.asyncio
async def test_segmenter_parses_environment_hint_en_from_llm(monkeypatch):
    """ENV-DISTILL — _call_llm 返回的 JSON 含 environment_hint_en 字段时，
    解析后挂到 segment.environment_hint_en。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "true")
    monkeypatch.setenv("SEGMENTER_API_KEY", "fake-key")
    import importlib
    from app.services import scene_segmenter_service as mod
    importlib.reload(mod)
    svc = mod.SceneSegmenterService()

    # mock LLM client 返回 JSON
    fake_response = type("R", (), {})()
    fake_response.choices = [type("C", (), {})()]
    fake_response.choices[0].message = type("M", (), {})()
    fake_response.choices[0].message.content = (
        '{"segments": [{"location": "工作室", "location_en": "small workshop", '
        '"start_marker": "林墨看着", "summary": "林墨熔毁芯片", '
        '"characters_present": ["林墨", "苏晚晴"], "mood": "night", '
        '"environment_hint_en": "a dim workbench lit by pale blue chip glow, '
        'scattered circuit components, window showing neon night street"}]}'
    )

    class FakeAsyncClient:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    return fake_response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    # patch _get_client
    svc._client = FakeAsyncClient()

    segs = await svc.segment_chapter(
        "x" * 2000,
        {"scene": "工作室", "summary": "林墨看着芯片", "characters": ["林墨"]},
    )
    assert len(segs) >= 1
    seg = segs[0]
    # environment_hint_en 必须被解析
    assert seg.environment_hint_en
    assert "workbench" in seg.environment_hint_en
    assert "blue chip glow" in seg.environment_hint_en
    # 不应该含人物语义（这是 LLM 输出的内容，断言它正确蒸馏了）
    assert "林墨" not in seg.environment_hint_en
    assert "person" not in seg.environment_hint_en.lower()
    assert "smile" not in seg.environment_hint_en.lower()


# ---------- 端到端：背景生成流程接入 segmenter ----------

@pytest.mark.asyncio
async def test_generate_chapter_backgrounds_uses_segmenter(monkeypatch):
    """统一接口：segmenter 返回多段 → 结果里应有各段场景名（analyzer 不能压成 1）。"""
    monkeypatch.setenv("SCENE_SEGMENTER_ENABLED", "true")
    monkeypatch.setenv("SEGMENTER_API_KEY", "fake-key")
    import importlib
    from app.services import scene_segmenter_service as seg_mod
    from app.services.scene_segmenter_service import SceneSegment
    importlib.reload(seg_mod)

    async def fake_segment(content, outline, force_single=False):
        return [
            SceneSegment(segment_id=1, location="朝堂", mood="day", characters_present=["李"]),
            SceneSegment(segment_id=2, location="御花园", mood="dusk", characters_present=["李"]),
        ]

    monkeypatch.setattr(seg_mod.scene_segmenter_service, "segment_chapter", fake_segment)

    from app.services import asset_management_service as ams_mod
    importlib.reload(ams_mod)
    monkeypatch.setattr(ams_mod, "scene_segmenter_service", seg_mod.scene_segmenter_service)

    class FakeGenResult:
        status = "completed"
        retry_count = 0
        fallback_type = None
        image_path = "/static/x.png"
        reason = None
        validation_results = []

    async def fake_gen_with_validation(**kw):
        return FakeGenResult()

    monkeypatch.setattr(
        ams_mod.image_generation_service,
        "generate_background_with_validation",
        fake_gen_with_validation,
    )

    class FakeQuery:
        def filter(self, *a, **kw):
            return self

        def first(self):
            return None

        def all(self):
            return []

    class FakeDB:
        def query(self, _):
            return FakeQuery()

        def add(self, _):
            pass

        def commit(self):
            pass

        def refresh(self, _):
            pass

        def flush(self):
            pass

        def rollback(self):
            pass

    svc = ams_mod.AssetManagementService(FakeDB())
    outline = type("O", (), {
        "scene": "朝堂", "summary": "s" * 50, "characters": ["李"], "visual_keywords": [],
        "conflict": "", "emotion": "day",
    })()
    bible = type("S", (), {"raw_json": {}, "characters": []})()
    svc._get_chapter_outline = lambda p, c: outline
    svc._get_chapter_content = lambda p, c: type("C", (), {"content": "正文" * 300})()
    svc._get_story_bible = lambda p: bible
    svc._ensure_visual_bible_locked = lambda p, sb: None
    svc._build_visual_style_profile = lambda p, sb: type("VP", (), {
        "project_genre": "historical",
        "to_dict": lambda self: {},
    })()
    svc._persist_background_asset_if_passed = (
        lambda *a, **kw: type("A", (), {"id": 1, "image_url": "/static/x.png"})()
    )
    svc._collect_forbidden_characters = lambda **kw: ["李"]

    from app.services.background_scene_analyzer_service import BackgroundSceneAnalyzerService

    async def fake_enrich(self, segments, **kw):
        return self._align_specs_to_segments(
            list(segments),
            [],
            outline=kw.get("chapter_outline") or {},
            forbidden_characters=["李"],
            forbidden_entities=[],
        )

    monkeypatch.setattr(BackgroundSceneAnalyzerService, "enrich_segments", fake_enrich)
    monkeypatch.setattr(
        ams_mod.visual_style_profile_service,
        "background_style_tags",
        lambda *a, **kw: [],
    )

    # Stub classifier imported inside generate_chapter_backgrounds_v2
    import app.services.background_style_classifier_service as clf_mod

    class FakeClassifier:
        async def classify_scene(self, spec, bible):
            return {}

    monkeypatch.setattr(clf_mod, "BackgroundStyleClassifierService", FakeClassifier)

    # _register_scene_treatment may be called — ignore failures via empty treatment
    if hasattr(ams_mod, "_register_scene_treatment"):
        monkeypatch.setattr(ams_mod, "_register_scene_treatment", lambda *a, **kw: None)

    result = await svc.generate_chapter_backgrounds(project_id=1, chapter_index=1)
    scene_names = [r.get("scene_name") for r in result.get("results", [])]
    assert result.get("total") == 2
    assert "朝堂" in scene_names
    assert "御花园" in scene_names

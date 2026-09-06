"""
L5.04 — Stage_Background_AR 场景切分测试。

5 个 SPLIT case 验证 analyzer 的切分逻辑：
- 日夜变化（同地点、不同 time_of_day）
- 天气变化（同地点、不同 weather）
- 物理状态变化（同地点、不同 physical_state）
- 人群状态变化（同地点、不同 people_policy mode）
- 对话不变（不切场景）
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.background_scene_analyzer_service import BackgroundSceneAnalyzerService
from app.services.background_scene_analyzer_service import (
    ANALYZER_CONTENT_CHAR_LIMIT,
    ANALYZER_MAX_SCENES,
    ANALYZER_RETRY_CONTENT_CHAR_LIMIT,
    ANALYZER_RETRY_OUTLINE_CHAR_LIMIT,
)
from app.services.asset_management_service import AssetManagementService


@pytest.fixture
def svc() -> BackgroundSceneAnalyzerService:
    return BackgroundSceneAnalyzerService()


def _make_spec(svc, env_text: str, time_of_day="night", weather="rain",
               scene_type="abandoned_void", mode="empty_required") -> dict:
    """构造 spec dict（未规范化）"""
    return {
        "scene_id": "s",
        "scene_name": "test",
        "scene_type": scene_type,
        "environment_description": env_text,
        "time_of_day": time_of_day,
        "weather": weather,
        "people_policy": {"mode": mode, "rationale": "test reason"},
    }


# -------------------- L5.04 --------------------


def test_split_case_day_night_change(svc):
    """同地点、日夜变化 → 应切场景（fingerprint 不同）"""
    spec_a = _make_spec(svc, "庭院白天场景" + "环境" * 50, time_of_day="noon")
    spec_b = _make_spec(svc, "庭院夜晚场景" + "环境" * 50, time_of_day="night")
    fp_a = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_a))
    fp_b = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_b))
    assert fp_a != fp_b, "日夜变化应导致 fingerprint 不同"


def test_split_case_weather_change(svc):
    """同地点、天气变化 → 应切场景"""
    spec_a = _make_spec(svc, "集市晴天" + "环境" * 50, weather="晴", scene_type="public_commerce", mode="background_groups_required")
    spec_b = _make_spec(svc, "集市暴雨" + "环境" * 50, weather="暴雨", scene_type="public_commerce", mode="background_groups_required")
    fp_a = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_a))
    fp_b = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_b))
    assert fp_a != fp_b


def test_split_case_physical_state_change(svc):
    """同地点、物理状态变化（整洁 → 废墟）→ 应切场景"""
    spec_a = _make_spec(svc, "庙宇整洁庄严" + "环境" * 50)
    spec_b = _make_spec(svc, "庙宇已成废墟" + "环境" * 50)
    fp_a = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_a))
    fp_b = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_b))
    assert fp_a != fp_b


def test_split_case_crowd_state_change(svc):
    """同地点、人群状态变化 → 应切场景"""
    spec_a = _make_spec(svc, "集市熙攘" + "环境" * 50, scene_type="public_commerce", mode="background_groups_required")
    spec_b = _make_spec(svc, "集市散场空荡" + "环境" * 50, scene_type="public_commerce", mode="empty_required")
    fp_a = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_a))
    fp_b = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_b))
    assert fp_a != fp_b


def test_no_split_case_dialogue_only(svc):
    """同环境、仅对话不同 → 不切场景（fingerprint 相同）"""
    spec_a = _make_spec(svc, "同一间书房" + "环境" * 50, scene_type="private_interior")
    spec_b = _make_spec(svc, "同一间书房" + "环境" * 50, scene_type="private_interior")
    fp_a = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_a))
    fp_b = AssetManagementService._scene_fingerprint(_to_spec_obj(spec_b))
    assert fp_a == fp_b


def test_dedup_keeps_different_locations_same_visual_state():
    """不同物理地点即使视觉状态相同，也不能被 dedup 合并。"""
    from app.schemas import BackgroundSceneSpec, PeoplePolicy

    def make(name: str, selector: str):
        return BackgroundSceneSpec(
            scene_id=selector,
            scene_name=name,
            scene_selector=selector,
            scene_type="private_interior",
            environment_description=(name + " 室内空间，桌椅陈设，窗外微光，空气安静。") * 8,
            lighting="柔和光线",
            time_of_day="night",
            atmosphere="安静",
            camera_shot_type="interior_wide",
            people_policy=PeoplePolicy(mode="empty_required", rationale="test reason"),
        )

    specs = [make("宿舍", "dorm_01"), make("机房", "lab_01")]
    deduped = AssetManagementService._dedup_scenes(None, specs)
    assert [s.scene_name for s in deduped] == ["宿舍", "机房"]


@pytest.mark.asyncio
async def test_fallback_splits_compound_scene_and_filters_people_keywords(svc, monkeypatch):
    """LLM 不可用 fallback：复合地点应拆分，人物/合照/屏幕人像词不得进入 env 描述。"""
    async def fake_segment(*args, **kwargs):
        from app.services.scene_segmenter_service import SceneSegment
        return [SceneSegment(segment_id=1, location="毕业典礼操场与回忆机房")]

    monkeypatch.setattr(svc._segmenter, "segment_chapter", fake_segment)
    specs = await svc._fallback_to_segmenter(
        chapter_content="",
        outline={
            "scene": "毕业典礼操场与回忆机房",
            "visual_keywords": [
                "无数学士帽飞向蓝天像一行行语义不明的代码",
                "老旧机房显示器映出四张褪去青涩的脸",
                "李明新工位桌角摆着那个小机器人挂坠和四人合照",
            ],
            "emotion": "希望",
            "conflict": "李明与赵月对视",
        },
        forbidden_chars=["李明", "赵月"],
    )

    assert [s.scene_name for s in specs] == ["毕业典礼操场", "回忆机房"]
    joined = "\n".join(s.environment_description for s in specs)
    assert "四张" not in joined
    assert "合照" not in joined
    assert "显示器映出" not in joined
    assert "李明" not in joined
    assert "赵月" not in joined
    assert "学士帽" in joined


@pytest.mark.asyncio
async def test_analyzer_fallback_skips_segmenter_llm_by_default(svc, monkeypatch):
    """analyzer fallback 默认不用 segmenter LLM，避免 analyzer timeout 后继续 timeout。"""
    called = {"n": 0}

    async def fake_segment(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("segmenter should not be called by analyzer fallback")

    monkeypatch.setattr(svc._segmenter, "segment_chapter", fake_segment)
    specs = await svc._fallback_to_segmenter(
        chapter_content="正文" * 1000,
        outline={"scene": "计算机机房", "summary": "机房报错", "visual_keywords": ["黑底绿字的终端报错"]},
        forbidden_chars=[],
    )

    assert called["n"] == 0
    assert len(specs) == 1
    assert specs[0].scene_name == "计算机机房"
    assert "黑底绿字的终端报错" in specs[0].environment_description


def test_split_validates_consecutive_fingerprints(svc):
    """_validate_split_reasons: 同 fingerprint + 缺 split_reason → 拒绝"""
    spec_a = _make_spec(svc, "书房场景" + "环境" * 50, scene_type="private_interior")
    spec_b = _make_spec(svc, "书房场景" + "环境" * 50, scene_type="private_interior")
    # 不给 split_reason
    ok, msg = svc._validate_split_reasons([spec_a, spec_b])
    assert not ok
    assert "split_reason" in msg or "duplicate" in msg


def test_split_accepts_with_reason(svc):
    """同 fingerprint + 有 split_reason → 接受"""
    spec_a = _make_spec(svc, "书房场景" + "环境" * 50, scene_type="private_interior")
    spec_b = _make_spec(svc, "书房场景" + "环境" * 50, scene_type="private_interior")
    spec_b["split_reason"] = "虽然同地点但镜头切换"
    ok, msg = svc._validate_split_reasons([spec_a, spec_b])
    assert ok


def test_analyzer_user_prompt_uses_compact_defaults(svc):
    """analyzer 首轮 prompt 应限制正文长度和 scene 数，降低 LLM timeout 概率。"""
    long_content = "正" * (ANALYZER_CONTENT_CHAR_LIMIT + 500)
    prompt = svc._render_user_prompt(
        chapter_content=long_content,
        outline={"summary": "摘要", "scene": "旧机房"},
        story_bible={},
        forbidden_chars=[],
    )

    assert prompt.count("正") == ANALYZER_CONTENT_CHAR_LIMIT
    assert "chapter text truncated for analyzer" in prompt
    assert f"最多输出 {ANALYZER_MAX_SCENES} 个 scenes" in prompt
    assert "120-220 字" in prompt


def test_analyzer_retry_prompt_is_more_compact(svc):
    """第二轮 timeout retry 不应重复发送同样大的 prompt。"""
    prompt = (
        "<ChapterOutline>\n" + "纲" * 2000 + "\n</ChapterOutline>\n"
        "<ChapterText>\n" + "文" * 5000 + "\n</ChapterText>\n"
    )

    compact = svc._compact_user_prompt_for_retry(prompt)

    assert compact.count("纲") == ANALYZER_RETRY_OUTLINE_CHAR_LIMIT
    assert compact.count("文") == ANALYZER_RETRY_CONTENT_CHAR_LIMIT
    assert "RetryInstruction" in compact
    assert len(compact) < len(prompt)


def test_analyzer_recovers_complete_scenes_from_truncated_json(svc):
    """LLM 输出被截断时，应尽量回收已完整闭合的 scene 对象。"""
    malformed = """
{
  "scenes": [
    {
      "scene_id": "s1",
      "scene_name": "计算机机房",
      "scene_type": "private_interior",
      "environment_description": "空置的计算机机房，机柜成排排列，黑底绿字终端停在报错界面，冷白荧光灯反射在防静电地板上。",
      "lighting": "冷白荧光灯",
      "time_of_day": "night",
      "atmosphere": "紧张冷清",
      "camera_shot_type": "interior_wide",
      "people_policy": {"mode": "empty_required", "rationale": "empty room"}
    },
    {
      "scene_id": "s2",
      "scene_name": "未闭合
"""

    data = svc._parse_llm_json_response(malformed)

    assert len(data["scenes"]) == 1
    assert data["scenes"][0]["scene_name"] == "计算机机房"


def test_analyzer_parse_wraps_top_level_list(svc):
    """少数模型直接返回数组时，解析器应包装成 scenes。"""
    data = svc._parse_llm_json_response('[{"scene_id":"s1","scene_name":"机房"}]')
    assert data == {"scenes": [{"scene_id": "s1", "scene_name": "机房"}]}


# -------------------- helpers --------------------


def _to_spec_obj(d: dict):
    """把 dict 转 BackgroundSceneSpec（用于 _scene_fingerprint）"""
    from app.schemas import BackgroundSceneSpec, PeoplePolicy
    env = (d.get("environment_description") or "") + "环境补充" * 30
    return BackgroundSceneSpec(
        scene_id=d.get("scene_id", "s"),
        scene_name=d.get("scene_name", "x"),
        scene_selector="test_slug_x",
        scene_type=d.get("scene_type", "abandoned_void"),
        environment_description=env,
        lighting="冷光",
        weather=d.get("weather"),
        time_of_day=d.get("time_of_day", "unknown"),
        atmosphere="test",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(
            mode=d.get("people_policy", {}).get("mode", "empty_required"),
            rationale=d.get("people_policy", {}).get("rationale", "r"),
        ),
    )

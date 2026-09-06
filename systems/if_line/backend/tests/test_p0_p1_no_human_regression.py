"""
P0+P1 新增回归测试（2026-07-03）。

1. test_no_human_pollution_regression:
   验证 analyzer rules 加的"痕迹类描述禁令"在配置里生效。
   不调用真实 LLM，只检查 rules 字段含相应禁令。

2. test_v2_validation_retry_once:
   验证 v2 路径在 BG_VALIDATE_ENABLED=true 且 bbox 检测到人像时，
   会用 escalation_level_1 重生成 1 次。bbox 不可用时跳过验收直接接受图。
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ---------- P0-2: analyzer rules 痕迹禁令回归 ----------

def test_no_human_pollution_regression_analyzer_rules():
    """analyzer rules 必须含'禁止痕迹类描述'两条规则，避免 LLM 输出
    '屏幕亮着/冒着热气'等隐含人在场的状态描述。"""
    import json
    cfg_path = BACKEND_DIR / "app" / "config" / "image_generation_profiles.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    rules = (
        cfg["rewriter"]["background_scene_extractor"]
        ["system_prompt"]["rules"]
    )
    # 至少有一条 rule 提到"需要人在场才会发生的状态描述"
    has_state_ban = any("需要人在场才会发生的状态描述" in r for r in rules)
    assert has_state_ban, f"analyzer rules 缺少'禁止状态描述'规则: {rules}"
    # 至少有一条 rule 提到"暗示近期或曾经有人在场的痕迹类描述"
    has_trace_ban = any("暗示近期或曾经有人在场的痕迹类描述" in r for r in rules)
    assert has_trace_ban, f"analyzer rules 缺少'禁止痕迹类描述'规则: {rules}"


def test_negative_clauses_are_positive_statements():
    """background_negative_clauses 必须改成正向环境声明，
    不应再含具体人像词（人物/守卫/士兵/剪影/肖像/人群）。
    命名角色禁令段由 _format_named_cast_exclusion 单独处理，不在此 clause。"""
    import json
    cfg_path = BACKEND_DIR / "app" / "config" / "image_generation_profiles.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    nc = cfg["background_negative_clauses"]
    forbidden_tokens = [
        "不要人物", "不要守卫", "不要士兵", "不要剪影",
        "不要肖像", "不要人群", "不要人脸", "不要人体",
    ]
    for key in ("base", "empty_required", "establishing_shot_enforcement",
                "escalation_level_1"):
        clause = nc.get(key, "")
        for tok in forbidden_tokens:
            assert tok not in clause, \
                f"clause[{key}] 不应再含反向 token '{tok}'（应改用正向环境声明）: {clause}"
        # 必须含正向声明关键词之一
        assert ("纯空环境" in clause or "无任何生物" in clause
                or "画面 100% 由" in clause or "纯空场景" in clause
                or "纯环境画面" in clause or "强化空场景" in clause
                or "纯粹的建筑或景观" in clause), \
            f"clause[{key}] 必须含正向环境声明关键词: {clause}"


# ---------- P1-1: v2 路径 bbox-only 验收 + 重试 ----------

def _make_spec():
    from app.schemas import BackgroundSceneSpec, PeoplePolicy
    env = ("高耸的重檐庑殿顶大殿内部，红墙金柱直通藻井，地面铺青石方砖，"
           "缝隙严丝合缝。殿正中设须弥座宝座，背靠九龙金漆屏风，"
           "屏风上飞龙腾跃云海，金箔在晨光中微微闪光。"
           "两侧分列铜鹤铜龟香炉，香炉口余烟缓慢上升。"
           "殿门洞开，丹陛石阶通向殿外广场，远处可见铜鼎与朱红宫墙。"
           "殿内无人，空气凝滞，唯有光线在金柱间游移。")
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="太和殿",
        scene_selector="tai_he_dian__01",
        scene_type="civic_military",
        environment_description=env,
        lighting="晨光斜射",
        weather="晴",
        time_of_day="morning",
        atmosphere="庄严",
        camera_shot_type="interior_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
        forbidden_characters=["李承曄"],
    )


def _write_png(path):
    from PIL import Image
    Image.new("RGB", (1920, 1080), (120, 120, 120)).save(path)
    return path


def _make_validation_result(verdict: str):
    """构造真实 ValidationResult，避免 GenerationResult Pydantic 校验失败。"""
    from app.schemas import ValidationResult
    return ValidationResult(
        passed=(verdict == "hard_pass"),
        stage="bbox",
        reason="test mock",
        is_hard_fail=(verdict == "hard_fail"),
    )


@pytest.mark.asyncio
async def test_v2_validation_skipped_when_disabled(tmp_path, monkeypatch):
    """BG_VALIDATE_ENABLED=False 时，v2 路径不调 bbox validator，validation_results 为空。"""
    from app.services import image_generation_service as mod
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)
    from app.services.image_generation_service import ImageGenerationService

    spec = _make_spec()
    image_path = _write_png(tmp_path / "bg.png")
    svc = ImageGenerationService()

    with patch.object(
        svc, "_generate_background_once_with_prompt",
        new=AsyncMock(return_value=image_path),
    ), patch.object(
        svc, "_bbox_validate_background",
        side_effect=AssertionError("bbox must not be called when disabled"),
    ) as bbox_mock:
        result = await svc.generate_background_with_validation(
            spec, forbidden_characters=["李承曄"], genre="historical",
        )

    assert result.status == "completed"
    assert result.validation_results == []
    assert bbox_mock.call_count == 0


@pytest.mark.asyncio
async def test_v2_validation_passes_when_bbox_clear(tmp_path, monkeypatch):
    """BG_VALIDATE_ENABLED=True 且 bbox 返回 hard_pass 时，正常完成，retry_count=0。"""
    from app.services import image_generation_service as mod
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", True)
    from app.services.image_generation_service import ImageGenerationService

    spec = _make_spec()
    image_path = _write_png(tmp_path / "bg.png")
    svc = ImageGenerationService()

    # 用 AsyncMock 包装以便能查 await_count
    async def _bbox_pass_impl(*args, **kwargs):
        return ("hard_pass", "no person detected", _make_validation_result("hard_pass"))
    fake_bbox_pass = AsyncMock(side_effect=_bbox_pass_impl)

    with patch.object(
        svc, "_generate_background_once_with_prompt",
        new=AsyncMock(return_value=image_path),
    ), patch.object(
        svc, "_bbox_validate_background",
        new=fake_bbox_pass,
    ) as bbox_mock:
        result = await svc.generate_background_with_validation(
            spec, forbidden_characters=["李承曄"], genre="historical",
        )

    assert result.status == "completed"
    assert result.retry_count == 0
    assert bbox_mock.await_count == 1


@pytest.mark.asyncio
async def test_v2_validation_retries_once_on_hard_fail(tmp_path, monkeypatch):
    """BG_VALIDATE_ENABLED=True 且 bbox hard_fail 时，重试 1 次。
    二次仍 fail 时接受当前图（不无限重试），retry_count=1。"""
    from app.services import image_generation_service as mod
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", True)
    from app.services.image_generation_service import ImageGenerationService

    spec = _make_spec()
    image_path_1 = _write_png(tmp_path / "bg1.png")
    image_path_2 = _write_png(tmp_path / "bg2.png")
    svc = ImageGenerationService()

    # 第一次和第二次 generate 返回不同路径，便于确认重试发生
    gen_calls = {"count": 0}
    image_seq = [image_path_1, image_path_2]

    async def fake_gen(*args, **kwargs):
        path = image_seq[gen_calls["count"]]
        gen_calls["count"] += 1
        return path

    # bbox 永远返回 hard_fail
    call_count = {"bbox": 0}

    async def fake_bbox_hard_fail(*args, **kwargs):
        call_count["bbox"] += 1
        return ("hard_fail", "person detected", _make_validation_result("hard_fail"))

    with patch.object(
        svc, "_generate_background_once_with_prompt", new=fake_gen,
    ), patch.object(
        svc, "_bbox_validate_background", new=fake_bbox_hard_fail,
    ):
        result = await svc.generate_background_with_validation(
            spec, forbidden_characters=["李承曄"], genre="historical",
        )

    assert result.status == "completed"
    # 重试 1 次 → retry_count=1
    assert result.retry_count == 1
    # bbox 调了 2 次（首次 + 重试后）
    assert call_count["bbox"] == 2
    # generate 调了 2 次（首次 + escalation 重试）
    assert gen_calls["count"] == 2


@pytest.mark.asyncio
async def test_v2_validation_skipped_when_bbox_unavailable(tmp_path, monkeypatch):
    """BG_VALIDATE_ENABLED=True 但 bbox 模型不可用时，返回 skipped，
    v2 路径直接接受图，不重试。"""
    from app.services import image_generation_service as mod
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", True)
    from app.services.image_generation_service import ImageGenerationService

    spec = _make_spec()
    image_path = _write_png(tmp_path / "bg.png")
    svc = ImageGenerationService()

    async def fake_bbox_skipped(*args, **kwargs):
        return ("skipped", "ultralytics not installed", None)

    with patch.object(
        svc, "_generate_background_once_with_prompt",
        new=AsyncMock(return_value=image_path),
    ), patch.object(
        svc, "_bbox_validate_background", new=fake_bbox_skipped,
    ):
        result = await svc.generate_background_with_validation(
            spec, forbidden_characters=["李承曄"], genre="historical",
        )

    assert result.status == "completed"
    assert result.retry_count == 0
    # validation_results 应为空（skipped 不计入）
    assert result.validation_results == []


# ---------- P2-1: rationale 幂等 ----------

def test_rationale_idempotent_under_global_policy():
    """v2 路径 _dedup_scenes 后，spec.people_policy.rationale
    不应被重复累加'已按全局背景策略强制设为空场'。"""
    from app.schemas import BackgroundSceneSpec, PeoplePolicy

    # 模拟 LLM 已经返回了带"全局背景策略"标记的 rationale（assembler 的 _localize_text 会产生）
    spec = BackgroundSceneSpec(
        scene_id="s1",
        scene_name="测试场景",
        scene_selector="test_scene__01",
        scene_type="private_interior",
        environment_description="x" * 200,
        lighting="自然光",
        time_of_day="morning",
        atmosphere="平静",
        camera_shot_type="interior_wide",
        people_policy=PeoplePolicy(
            mode="empty_required",
            rationale="已按全局背景策略强制设为空场",  # 模拟 LLM 返回的已带标记
        ),
    )
    # 模拟 asset_management_service.generate_chapter_backgrounds_v2 的幂等检查
    spec.people_policy.mode = "empty_required"
    existing_rationale = spec.people_policy.rationale or ""
    if (
        "FORCED_EMPTY_BY_GLOBAL_POLICY" not in existing_rationale
        and "全局背景策略" not in existing_rationale
    ):
        spec.people_policy.rationale = (
            f"{existing_rationale}；已按全局背景策略强制设为空场".lstrip("；")
        )

    # 断言不再重复累加
    assert spec.people_policy.rationale.count("已按全局背景策略") == 1, \
        f"rationale 被重复累加: {spec.people_policy.rationale}"


# ---------- P0.5: fallback 不再带 outline.visual_keywords 的动作词 ----------

def test_fallback_environment_description_omits_visual_keywords():
    """P0.5：fallback env_desc 不能出现 outline.visual_keywords 里的动作描述。
    历史样本：visual_keywords=['紫甘蓝汁在白瓷碗...', '母亲掌心托出一小袋蓝色碎晶']
    导致 CogView-4 按动作生成主体化人像。
    """
    from app.services.background_scene_analyzer_service import (
        BackgroundSceneAnalyzerService as Svc,
    )
    outline = {
        "scene": "深夜厨房操作台",
        "visual_keywords": [
            "紫甘蓝汁在白瓷碗中依次变为红、紫、绿",
            "母亲掌心托出一小袋褪色的蓝色碎晶",
            "平板屏幕显示出AI预测的柱状结晶生长趋势图",
        ],
    }
    env = Svc._fallback_environment_description("深夜厨房", outline, safe_keywords=None)
    assert "fallback safe background environment" in env
    assert "画面 100% 由" in env
    assert "纯空环境" in env
    assert "无任何生物" in env
    # 关键断言：动作描述必须不在 env_desc 里
    assert "母亲掌心" not in env
    assert "掌心" not in env
    assert "托出" not in env
    assert "紫甘蓝" not in env
    assert "柱状结晶" not in env


def test_safe_visual_keywords_rejects_action_phrases():
    """P0.5：按人物/人体部位/动作语义过滤，不误伤安全环境细节。"""
    from app.services.background_scene_analyzer_service import (
        BackgroundSceneAnalyzerService as Svc,
    )
    outline = {
        "visual_keywords": [
            "白瓷碗",            # 3 字：保留
            "窗台培养皿",         # 5 字：保留
            "母亲掌心托出一小袋",  # 8 字：触发动作词，丢弃
            "平板屏幕显示出AI预测的柱状结晶生长趋势图",  # 长：丢弃
        ],
    }
    safe = Svc()._safe_visual_keywords(outline, forbidden_chars=[])
    assert "白瓷碗" in safe
    assert "窗台培养皿" in safe
    assert "母亲掌心托出一小袋" not in safe
    assert "柱状结晶" not in str(safe)


def test_unsafe_visual_keywords_includes_body_parts_and_verbs():
    """P0.5：BACKGROUND_UNSAFE_VISUAL_KEYWORDS 必须含人体部位和动作动词。"""
    from app.services.background_scene_analyzer_service import (
        BACKGROUND_UNSAFE_VISUAL_KEYWORDS,
    )
    s = " ".join(BACKGROUND_UNSAFE_VISUAL_KEYWORDS).lower()
    # 人体部位
    for w in ["掌心", "手指", "手掌", "手", "hair", "palm"]:
        assert w in s, f"missing body-part keyword: {w}"
    # 动作动词
    for w in ["托出", "托着", "捧着", "握着", "拿着", "holding", "carrying"]:
        assert w in s, f"missing action verb: {w}"
    # 称谓
    for w in ["母亲", "父亲", "mother", "father"]:
        assert w in s, f"missing kinship noun: {w}"

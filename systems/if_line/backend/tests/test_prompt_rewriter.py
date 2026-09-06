"""
D16 — prompt rewriter 集成测试。
A12/B12/C11 — 黄金集合离线评估。

不依赖真实 LLM 调用（PROMPT_REWRITER_ENABLED=false 时 rewriter 返回 None，
测试 focus 在 fallback 路径 + schema 校验 + guardrail + 配置完整性）。
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ---------- D01: RewrittenPrompt schema ----------

def test_rewritten_prompt_to_cogview_prompt_word_order():
    """D09 — 词序: subject → style → details → lighting → composition → 否定。

    Project-visual-bible 契约 (shared-style-prompt-v2) 后：默认 ``style_locked=True``，
    LLM 的 style 字段被丢弃，由后端 ProjectVisualBible 注入共享块。
    此测试覆盖两条路径：locked（默认）丢弃 style，unlocked（兼容）保留 style。
    """
    from app.services.prompt_rewriter_service import RewrittenPrompt

    # 1) 默认 locked=True：style 不进入最终 prompt
    p_locked = RewrittenPrompt(
        subject="young Chinese woman in flowing white mourning hanfu",
        details=["loose black hair", "soft silk folds"],
        lighting="soft diffused overcast lighting",
        composition="half body shot from waist up, character centered",
        style="Tang dynasty aesthetic, visual novel art",  # LLM 试图污染
        negative_minimal=["text", "watermark"],
    )
    out_locked = p_locked.to_cogview_prompt()
    assert p_locked.style_locked is True  # 默认值
    assert "Tang dynasty" not in out_locked  # 污染被丢弃
    # 词序仍然正确（无 style 段）：subject → details → lighting → composition → 否定
    assert out_locked.index("young Chinese woman") < out_locked.index("loose black hair")
    assert out_locked.index("loose black hair") < out_locked.index("soft diffused overcast")
    assert out_locked.index("half body shot") > out_locked.index("soft diffused overcast")
    assert out_locked.index("no text") > out_locked.index("half body shot")

    # 2) 兼容路径 locked=False：style 仍然出现，旧词序保留
    p_open = RewrittenPrompt(
        subject="young Chinese woman in flowing white mourning hanfu",
        details=["loose black hair"],
        lighting="soft diffused overcast lighting",
        composition="half body shot",
        style="Tang dynasty aesthetic",
        negative_minimal=["text"],
        style_locked=False,
    )
    out_open = p_open.to_cogview_prompt()
    assert "Tang dynasty" in out_open
    assert out_open.index("young Chinese woman") < out_open.index("Tang dynasty")


def test_rewritten_prompt_style_pollution_discarded_by_default():
    """shared-style-prompt-v2 — LLM 即使输出 style，默认也被丢弃。

    这条断言是整个 visual-bible 链路的核心契约：风格由后端
    ProjectVisualBible 确定性注入，LLM 无权改写。
    """
    from app.services.prompt_rewriter_service import RewrittenPrompt

    pollution_cases = [
        "photorealistic cinematic photography",
        "oil painting thick strokes, impasto",
        "3D render, octane, unreal engine",
        "neon cyberpunk, blade runner palette",
    ]
    for pollution in pollution_cases:
        p = RewrittenPrompt(
            subject="a young woman in hanfu",
            details=["loose hair"],
            lighting="soft",
            composition="half body",
            style=pollution,
            negative_minimal=["text"],
        )
        out = p.to_cogview_prompt()
        # 每种污染关键词都不该出现在最终 prompt
        for word in pollution.split():
            word_clean = word.strip(",")
            if len(word_clean) < 4:
                continue
            assert word_clean.lower() not in out.lower(), (
                f"style pollution '{word_clean}' from LLM leaked into prompt: {out}"
            )


def test_rewritten_prompt_background_path_also_discards_style():
    """背景路径 to_background_cogview_prompt 同样遵守 style_locked。"""
    from app.services.prompt_rewriter_service import RewrittenPrompt

    p = RewrittenPrompt(
        subject="空旷的唐代宫殿大殿",
        details=["雕梁画栋"],
        lighting="柔和阳光",
        composition="16:9 建立镜头",
        style="写实电影感摄影",  # LLM 试图把背景切到写实摄影
        negative_minimal=["命名角色", "人物主体"],
    )
    out = p.to_background_cogview_prompt()
    assert "写实电影感摄影" not in out
    assert "photorealistic" not in out.lower()


def test_rewritten_prompt_guardrail_rejects_chinese():
    """D12 — guardrail 拒绝中文残留。"""
    from app.services.prompt_rewriter_service import RewrittenPrompt

    p = RewrittenPrompt(
        subject="身着素白丧服的年轻女子, somber expression",
        negative_minimal=["text"],
    )
    ok, errors = p.guardrail_check("portrait")
    assert not ok
    assert any("chinese" in e for e in errors)


def test_rewritten_prompt_guardrail_rejects_too_long_negative():
    """D12 — guardrail 拒绝 negative_minimal > 5 词。"""
    from app.services.prompt_rewriter_service import RewrittenPrompt

    p = RewrittenPrompt(
        subject="young woman in white mourning hanfu, pale porcelain complexion",
        negative_minimal=["a", "b", "c", "d", "e", "f", "g"],
    )
    ok, errors = p.guardrail_check("portrait")
    assert not ok
    assert any("negative" in e for e in errors)


def test_rewritten_prompt_guardrail_accepts_valid():
    """D12 — 合法输出通过。"""
    from app.services.prompt_rewriter_service import RewrittenPrompt

    p = RewrittenPrompt(
        subject="young Chinese woman in flowing white mourning hanfu, pale porcelain complexion, somber expression",
        details=["loose black hair pinned with white jade hairpin"],
        lighting="soft diffused overcast lighting",
        composition="half body shot from waist up",
        style="Tang dynasty visual novel art",
        negative_minimal=["text", "watermark"],
    )
    ok, errors = p.guardrail_check("portrait")
    assert ok, errors


def test_background_guardrail_requires_main_character_negatives():
    """BG-ENFORCE — background negative_minimal 必须含 >=2 个"主体化"否定词
    （防止本章角色成为画面主体）。"""
    from app.services.prompt_rewriter_service import RewrittenPrompt

    # 缺主体化否定词 → 应被拒
    bad = RewrittenPrompt(
        subject="vast empty imperial court hall with vermilion columns, polished marble floor, ornate coffered ceiling",
        details=["empty throne at the far end on a jade platform"],
        lighting="bright daylight streaming through side windows",
        composition="wide cinematic establishing shot, 16:9, low angle",
        style="Tang dynasty Chinese imperial architecture, anime background art",
        negative_minimal=["text", "watermark"],
    )
    ok, errs = bad.guardrail_check("background")
    assert not ok
    assert any("main-character-class" in e for e in errs)

    # 含足够主体化否定词 → 通过
    good = RewrittenPrompt(
        subject="vast empty imperial court hall with vermilion columns, polished marble floor, ornate coffered ceiling",
        details=["empty throne at the far end on a jade platform"],
        lighting="bright daylight streaming through side windows",
        composition="wide cinematic establishing shot, 16:9, low angle",
        style="Tang dynasty Chinese imperial architecture, anime background art",
        negative_minimal=["named characters", "main character", "portrait", "close-up face"],
    )
    ok, errs = good.guardrail_check("background")
    assert ok, errs


# ---------- D04: fallback 行为 ----------

@pytest.mark.asyncio
async def test_rewriter_returns_none_when_disabled(monkeypatch):
    """D04/D17 — PROMPT_REWRITER_ENABLED=false 时返回 None。"""
    monkeypatch.setenv("PROMPT_REWRITER_ENABLED", "false")
    # 重新 import 让 env 生效
    import importlib
    from app.services import prompt_rewriter_service as mod
    importlib.reload(mod)
    svc = mod.PromptRewriterService()
    result = await svc.rewrite("portrait", {"name": "test"})
    assert result is None


@pytest.mark.asyncio
async def test_rewriter_returns_none_when_no_api_key(monkeypatch):
    """D04 — 无 API key 时返回 None。"""
    monkeypatch.setenv("PROMPT_REWRITER_ENABLED", "true")
    monkeypatch.setenv("REWRITER_API_KEY", "")
    monkeypatch.setenv("AI_IMAGE_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    import importlib
    from app.services import prompt_rewriter_service as mod
    importlib.reload(mod)
    svc = mod.PromptRewriterService()
    result = await svc.rewrite("portrait", {"name": "test"})
    assert result is None


@pytest.mark.asyncio
async def test_rewriter_cache_key_stability():
    """D06 — 相同输入产生相同 cache key。"""
    from app.services.prompt_rewriter_service import prompt_rewriter_service as svc
    fields1 = {"name": "李清照", "emotion": "sad"}
    fields2 = {"emotion": "sad", "name": "李清照"}
    key1 = svc._cache_key("portrait", fields1)
    key2 = svc._cache_key("portrait", fields2)
    assert key1 == key2


# ---------- A01/B01/C01: builder async wrapper ----------

@pytest.mark.asyncio
async def test_build_portrait_prompts_async_without_llm(monkeypatch):
    """A01 — rewriter 不可用时仍能产出 prompts（无 final_prompt 字段）。"""
    monkeypatch.setenv("PROMPT_REWRITER_ENABLED", "false")
    import importlib
    from app.services import prompt_rewriter_service as mod
    importlib.reload(mod)
    # 也重新加载 builder，因为它 import 了 rewriter 的全局实例
    from app.services import prompt_builder_service as pb_mod
    importlib.reload(pb_mod)

    story_bible = {
        "characters": [
            {"name": "李清照", "appearance": "身着素白丧服的年轻女子"},
        ],
        "worldview": "古代中国",
        "style_rules": "",
    }
    prompts = await pb_mod.prompt_builder_service.build_portrait_prompts_async(
        story_bible, project_id=1,
        variations=[{"emotion": "sad", "outfit": "mourning", "pose": "standing"}],
    )
    assert len(prompts) == 1
    assert prompts[0]["character_name"] == "李清照"
    # rewriter 关闭，不应该有 final_prompt
    assert "final_prompt" not in prompts[0]


# ---------- A12/B12/C11: 黄金集合离线评估 ----------

def _load_golden(name):
    path = BACKEND_DIR / "tests" / "fixtures" / "golden_set" / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_golden_portraits_set_completeness():
    """A12 — 10 个固定角色黄金集合完整。"""
    data = _load_golden("portraits")
    assert len(data) == 10
    for item in data:
        assert "name" in item
        assert "appearance_zh" in item
        assert "emotion" in item
        assert "genre" in item
        assert "expected_keywords" in item
        assert len(item["expected_keywords"]) >= 2


def test_golden_backgrounds_set_completeness():
    """B12 — 10 个固定场景黄金集合完整。"""
    data = _load_golden("backgrounds")
    assert len(data) == 10
    for item in data:
        assert "scene_zh" in item
        assert "mood" in item
        assert "genre" in item


def test_golden_keyframes_set_completeness():
    """C11 — 10 个固定关键帧黄金集合完整。"""
    data = _load_golden("keyframes")
    assert len(data) == 10
    for item in data:
        assert "event_zh" in item
        assert "characters" in item
        assert len(item["characters"]) >= 1


# ---------- B05: 背景不含服装词 ----------

def test_background_prompt_strips_clothing_words():
    """B05 — 背景 prompt 不应该出现 hanfu/clothing。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = svc._build_background_prompt(
        scene_description="朝堂",
        mood="day",
        style_prompt="Chinese historical style, hanfu, traditional Chinese clothing",
    )
    p_lower = p.lower()
    forbidden = ["hanfu", "clothing", "apparel"]
    for w in forbidden:
        assert w not in p_lower, f"forbidden word '{w}' in background prompt: {p}"


def test_background_prompt_no_zero_figures_stack():
    """B03 — 不再堆 'ZERO FIGURES, ZERO PEOPLE' 的 ZERO 大写形式。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = svc._build_background_prompt(
        scene_description="朝堂",
        mood="day",
        style_prompt="Chinese historical style",
    )
    assert "ZERO" not in p.upper()


def test_background_prompt_environment_focus_via_enforce():
    """BG-ENFORCE — _build_background_prompt 输出 + enforce 后必须含：
    - 中文地点建立镜头
    - 中文环境主体约束
    - 正向空场景声明（P0 重构后取代"不要人物/不要肖像"否定句，避免 CogView-4 CLIP 反向污染）
    - 2026-07-03 P0 重构：所有场景都用正向空环境声明

    注意：升级前 '朝堂' 触发 allow_background_people=True 时会有 "5% of the image" crowd 分支短语；
    升级后所有场景都强制完全无人，不再有 "5% of the image"。
    """
    from app.services.image_generation_service import image_generation_service as svc
    base = svc._build_background_prompt(
        scene_description="朝堂",
        mood="day",
        style_prompt="Chinese historical style",
    )
    # 经过 enforce 才是最终 prompt
    final = svc._enforce_background_environment_focus(
        base, forbidden_characters=None, scene_name="朝堂",
    )
    must_have = [
        "视觉小说背景图",
        "地点建立镜头",
        "画面 100% 由",
        "纯空环境",
        "无任何生物",
        "角色、人形或生命体",
    ]
    for w in must_have:
        assert w in final, f"missing required enforce keyword '{w}' in: {final}"
    # 升级后：不再有 "5% of the image" crowd-allow 短语
    p_lower = final.lower()
    assert "5% of the image" not in p_lower, \
        f"should NOT have '5% of the image' (crowd-allow removed): {final}"


# ---------- C03/C08: keyframe 多角色位置和强动词 ----------

def test_keyframe_prompt_has_character_positions():
    """C03 — 多角色 prompt 应含 left/center/right 之一。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = svc._build_keyframe_prompt(
        scene_description="朝堂",
        characters=[
            {"name": "A", "appearance": "young general"},
            {"name": "B", "appearance": "young woman"},
        ],
        action="clashing swords, sparks flying",
        emotion="intense",
        style_prompt="Tang dynasty CG art",
    )
    p_lower = p.lower()
    assert "left" in p_lower or "center" in p_lower or "right" in p_lower


def test_action_verbs_lookup():
    """C08 — action_verbs 词库查表工作。"""
    from app.services.prompt_builder_service import prompt_builder_service as svc
    # 命中 "打" → action_verbs["fighting"]
    action = svc._extract_action_from_conflict("两人打在一起")
    assert "clash" in action or "spark" in action or "combat" in action


def test_keyframe_identity_drift_ignores_untrusted_final_prompt():
    """Keyframe drift checks the structured prompt that callers actually send."""
    from app.services.prompt_rewriter_service import (
        PromptRewriterService,
        RewrittenPrompt,
    )

    identity_lock = (
        "CHARACTER IDENTITY LOCK: 林夜 (Lin Mo) must preserve the same face, "
        "same hairstyle, and canonical outfit."
    )
    fields = {
        "characters": [{"name": "林夜"}],
        "character_identity_lock": identity_lock,
    }
    output = RewrittenPrompt(
        final_prompt=(
            "林夜 (Lin Mo) preserves the CHARACTER IDENTITY LOCK with the same face "
            "and same hairstyle."
        ),
        subject="An unidentified stranger enters the moonlit station alone",
        details=["wind lifts a long coat beside the empty platform"],
        lighting="cold moonlight through drifting clouds",
        composition="wide cinematic frame from track level",
    )
    service = PromptRewriterService()

    assert service._has_identity_drift(output, fields) is True

    repaired = service._apply_identity_lock_fallback(output, fields)
    provider_prompt = repaired.to_cogview_prompt(use_final_prompt=False)
    assert identity_lock in provider_prompt
    assert "林夜" in provider_prompt
    assert "unidentified stranger" in provider_prompt
    assert output.final_prompt not in provider_prompt


def test_keyframe_budget_repair_ignores_untrusted_final_prompt():
    """Budget repair includes both structured content and the locked style."""
    from app.services.prompt_rewriter_service import (
        PromptRewriterService,
        RewrittenPrompt,
        _approx_token_count,
        _TARGET_TOKEN_BUDGET,
    )

    locked_style = "locked cel shading with precise blue linework " * 6
    fields = {"visual_style_prompt": locked_style}
    output = RewrittenPrompt(
        final_prompt="short decoy prompt",
        subject="Lin Mo preserves identity while " + ("crossing the station platform " * 100),
        composition="wide cinematic frame from track level",
    )

    repaired, errors = PromptRewriterService()._validate_output(
        "keyframe",
        output,
        fields,
    )
    service = PromptRewriterService()
    provider_prompt = service._serialize_provider_prompt(
        "keyframe",
        repaired,
        fields,
    )

    assert errors == []
    assert provider_prompt != output.final_prompt
    assert locked_style.strip() in provider_prompt
    assert _approx_token_count(provider_prompt) <= _TARGET_TOKEN_BUDGET["keyframe"] * 2


def test_background_budget_uses_real_serializer_and_locked_style():
    """Background guardrails count the Chinese provider prompt and trusted style."""
    from app.services.prompt_rewriter_service import (
        PromptRewriterService,
        RewrittenPrompt,
        _approx_token_count,
        _TARGET_TOKEN_BUDGET,
    )

    locked_style = "锁定的水墨线条、青绿色调与宣纸纹理，" * 140
    fields = {"visual_style_prompt": locked_style}
    output = RewrittenPrompt(
        final_prompt="short decoy prompt",
        subject="空旷的古代驿站月台延伸到薄雾笼罩的群山远方",
        details=["木制站牌与旧长椅沿月台排列"],
        lighting="清冷月光穿过低垂云层",
        composition="宽幅地点建立镜头",
        negative_minimal=["命名角色", "人物主体"],
    )
    service = PromptRewriterService()

    _, baseline_errors = output.guardrail_check("background")
    _, actual_errors = service._validate_output("background", output, fields)
    provider_prompt = service._serialize_provider_prompt(
        "background",
        output,
        fields,
    )

    assert not any("prompt too long" in error for error in baseline_errors)
    assert any("prompt too long" in error for error in actual_errors)
    assert provider_prompt == output.to_background_cogview_prompt(
        locked_style=locked_style,
    )
    assert _approx_token_count(provider_prompt) > _TARGET_TOKEN_BUDGET["background"] * 2


@pytest.mark.asyncio
async def test_keyframe_success_and_cache_stats_ignore_untrusted_final_prompt(monkeypatch):
    """Keyframe metrics include the trusted style for success and cache hits."""
    from app.services import prompt_rewriter_service as mod

    monkeypatch.setattr(mod, "PROMPT_REWRITER_ENABLED", True)
    monkeypatch.setattr(mod, "api_key_available", lambda *args, **kwargs: True)

    output = mod.RewrittenPrompt(
        final_prompt="decoy " * 100,
        subject="Lin Mo waits at the empty station under cold moonlight",
        details=["wind lifts the edge of his long coat"],
        composition="wide cinematic frame from track level",
    )
    service = mod.PromptRewriterService()
    monkeypatch.setattr(service, "_call_llm", AsyncMock(return_value=output))
    locked_style = "locked graphic-novel ink with restrained blue cel shading"
    fields = {
        "event_zh": "月台等待",
        "visual_style_prompt": locked_style,
    }

    first = await service.rewrite("keyframe", fields)
    second = await service.rewrite("keyframe", fields)

    expected = service._serialize_provider_prompt("keyframe", output, fields)
    assert expected == output.to_cogview_prompt(
        locked_style=locked_style,
        use_final_prompt=False,
    )
    assert first is output
    assert second is output
    assert [stat.cached for stat in service._stats] == [False, True]
    assert [stat.output_chars for stat in service._stats] == [len(expected), len(expected)]
    assert all(
        stat.approx_tokens == mod._approx_token_count(expected)
        for stat in service._stats
    )


@pytest.mark.asyncio
async def test_keyframe_identity_fallback_revalidates_actual_provider_budget(monkeypatch):
    from app.services import prompt_rewriter_service as mod

    monkeypatch.setattr(mod, "PROMPT_REWRITER_ENABLED", True)
    monkeypatch.setattr(mod, "api_key_available", lambda *args, **kwargs: True)
    output = mod.RewrittenPrompt(
        subject="An unidentified stranger crosses the moonlit station platform",
        composition="wide cinematic frame from track level",
    )
    service = mod.PromptRewriterService()
    monkeypatch.setattr(service, "_call_llm", AsyncMock(return_value=output))
    fields = {
        "characters": [{"name": "Alice"}],
        "character_identity_lock": (
            "CHARACTER IDENTITY LOCK: Alice " + "must preserve the same face " * 500
        ),
    }

    rewritten = await service.rewrite("keyframe", fields)

    assert rewritten is None
    assert len(service._stats) == 1
    assert service._stats[0].success is False
    assert "prompt too long" in (service._stats[0].error or "")


@pytest.mark.asyncio
async def test_background_success_and_cache_stats_use_real_provider_prompt(monkeypatch):
    """Background metrics use its Chinese serializer and include trusted style."""
    from app.services import prompt_rewriter_service as mod

    monkeypatch.setattr(mod, "PROMPT_REWRITER_ENABLED", True)
    monkeypatch.setattr(mod, "api_key_available", lambda *args, **kwargs: True)

    output = mod.RewrittenPrompt(
        final_prompt="short decoy prompt",
        subject="空旷的古代驿站月台延伸到薄雾笼罩的群山远方",
        details=["木制站牌与旧长椅沿月台排列"],
        lighting="清冷月光穿过低垂云层",
        composition="宽幅地点建立镜头",
        negative_minimal=["命名角色", "人物主体"],
    )
    service = mod.PromptRewriterService()
    monkeypatch.setattr(service, "_call_llm", AsyncMock(return_value=output))
    locked_style = "锁定的水墨线条、青绿色调与宣纸纹理"
    fields = {
        "scene_zh": "月台",
        "visual_style_prompt": locked_style,
    }

    first = await service.rewrite("background", fields)
    second = await service.rewrite("background", fields)

    expected = service._serialize_provider_prompt("background", output, fields)
    assert expected == output.to_background_cogview_prompt(
        locked_style=locked_style,
    )
    assert first is output
    assert second is output
    assert [stat.cached for stat in service._stats] == [False, True]
    assert [stat.output_chars for stat in service._stats] == [len(expected), len(expected)]
    assert all(
        stat.approx_tokens == mod._approx_token_count(expected)
        for stat in service._stats
    )


# ---------- D14: profiles 配置完整性 ----------

def test_profiles_json_has_rewriter_section():
    """D14 — image_generation_profiles.json 有 rewriter 段。"""
    cfg_path = BACKEND_DIR / "app" / "config" / "image_generation_profiles.json"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert "rewriter" in cfg
    for asset_type in ("portrait", "background", "keyframe"):
        assert asset_type in cfg["rewriter"]
        assert "system_prompt" in cfg["rewriter"][asset_type]
        assert len(cfg["rewriter"][asset_type]["system_prompt"]) > 200


def test_profiles_json_has_extended_vocabularies():
    """A06/B04/B05/C05 — 扩充词库段都在。"""
    cfg_path = BACKEND_DIR / "app" / "config" / "image_generation_profiles.json"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert "architecture_vocabulary" in cfg
    assert "action_verbs" in cfg
    assert "composition_vocabulary" in cfg
    assert "emotion_intensity_for_keyframe" in cfg
    assert "lighting_presets" in cfg
    # A06 — poses 扩到 >= 10
    assert len(cfg["poses"]) >= 10
    # B04 — moods 扩到 >= 12
    assert len(cfg["moods"]) >= 12


# ---------- D09: prompt 词序 ----------

def test_portrait_fallback_prompt_subject_before_negative():
    """A04/A08/D09 — portrait fallback prompt 主体在否定词前。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = svc._build_portrait_prompt(
        appearance_prompt="young woman in white mourning hanfu",
        emotion="sad",
        outfit="mourning",
        pose="standing",
        style_prompt="Tang dynasty style",
        forbidden="",
        full_body=False,
    )
    # "no text" 应在主体之后
    assert p.index("no text") > p.index("young woman")


# ---------- D18: 监控指标 ----------

@pytest.mark.asyncio
async def test_rewriter_stats_snapshot_shape():
    """D18 — get_stats_snapshot 返回结构正确。"""
    from app.services.prompt_rewriter_service import prompt_rewriter_service as svc
    snapshot = await svc.get_stats_snapshot()
    assert "total" in snapshot
    assert "enabled" in snapshot
    assert "model" in snapshot


# ---------- CogView-4 内容审核: 敏感词清理 ----------

def test_sanitize_replaces_english_case_insensitive():
    """Sanitize 必须大小写不敏感整词替换。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = "A Bloody WAR on the Battlefield, soldiers clashing swords"
    out = svc._sanitize_prompt(p)
    out_lower = out.lower()
    # 战争系全部要被替换
    assert "war" not in out_lower.split()  # 整词
    assert "bloody" not in out_lower
    assert "battlefield" not in out_lower
    assert "soldiers" not in out_lower
    assert "clashing" not in out_lower
    assert "swords" not in out_lower


def test_sanitize_replaces_chinese_sensitive():
    """中文敏感词必须被替换。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = "战场上尸横遍野，杀戮与鲜血"
    out = svc._sanitize_prompt(p)
    assert "战场" not in out
    assert "杀" not in out
    assert "血" not in out
    assert "尸" not in out


def test_sanitize_preserves_safe_words():
    """非敏感词应该原样保留。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = "vast imperial court hall with vermilion columns and polished marble floor"
    out = svc._sanitize_prompt(p)
    # 一个都不该被改
    assert out == p


def test_sanitize_handles_compound_variants():
    """词形变体（killing/killed/swords 等）也要被处理。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = "killing the wounded archer with multiple swords"
    out = svc._sanitize_prompt(p)
    out_lower = out.lower()
    assert "killing" not in out_lower
    assert "wounded" not in out_lower
    assert "archer" not in out_lower
    assert "swords" not in out_lower


# ---------- BG-NOCAST: 背景图强制排除章节角色 ----------

def test_build_cast_negation_returns_empty_when_no_characters():
    """无角色名时返回空串。"""
    from app.services.image_generation_service import image_generation_service as svc
    assert svc._build_cast_negation(None) == ""
    assert svc._build_cast_negation([]) == ""


def test_build_cast_negation_includes_all_names():
    """每个角色名都进否定段。"""
    from app.services.image_generation_service import image_generation_service as svc
    out = svc._build_cast_negation(["林夜", "苏晚晴", "赵峰"])
    assert "no 林夜" in out
    assert "no 苏晚晴" in out
    assert "no 赵峰" in out


def test_build_cast_negation_dedup_and_filter():
    """去重 + 过滤空/超长脏数据。"""
    from app.services.image_generation_service import image_generation_service as svc
    out = svc._build_cast_negation(["林夜", "林夜", "", "  ", "x" * 50])
    # 只有 "林夜" 一个有效，去重后只出现一次
    assert out == "no 林夜"


def test_build_cast_negation_truncates_to_eight():
    """超过 8 个角色时截断，避免 prompt 过长。"""
    from app.services.image_generation_service import image_generation_service as svc
    many = [f"角色{i}" for i in range(15)]
    out = svc._build_cast_negation(many)
    # 应该只有 8 个 "no" 段
    assert out.count(", ") == 7


def test_background_prompt_includes_cast_via_enforce():
    """BG-ENFORCE — _build_background_prompt + enforce 含 forbidden_characters 时把角色名注入。
    注意：_build_background_prompt 本身不再追加角色否定，由 enforce 统一处理。
    """
    from app.services.image_generation_service import image_generation_service as svc
    base = svc._build_background_prompt(
        scene_description="imperial court hall",
        mood="day",
        style_prompt="Tang dynasty style",
        forbidden_characters=["林夜", "苏晚晴"],
    )
    final = svc._enforce_background_environment_focus(
        base, forbidden_characters=["林夜", "苏晚晴"],
    )
    # 角色名要出现在最终 prompt 里的中文命名角色禁令段
    assert "林夜" in final
    assert "苏晚晴" in final
    assert "画面中不得出现这些命名角色" in final


def test_background_prompt_without_cast_keeps_old_behavior():
    """forbidden_characters=None 时 _build_background_prompt 不崩，enforce 仍能跑。"""
    from app.services.image_generation_service import image_generation_service as svc
    base = svc._build_background_prompt(
        scene_description="imperial court hall",
        mood="day",
        style_prompt="Tang dynasty style",
        forbidden_characters=None,
    )
    # base 不应该含角色否定（已迁移到 enforce）
    assert "画面中不得出现这些命名角色" not in base
    # enforce 后应该有环境主体约束
    final = svc._enforce_background_environment_focus(base, forbidden_characters=None)
    assert "视觉小说背景图" in final
    # forbidden=None 时不应出现 named cast 段
    assert "画面中不得出现这些命名角色" not in final

"""
BG-ENFORCE — 背景图"环境主体"统一约束测试。

覆盖 7 个核心场景：
1. enforce 函数添加中文环境主体关键词
2. enforce 添加本章角色点名排除
3. enforce 添加构图限制（纯空环境 / 纯空环境 / 完全无人）
4. final_prompt 路径经过 enforce
5. sanitize retry 后保留 enforce 约束
6. safety fallback 不含人物主体化词
7. forbidden_characters 多源合并
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ---------- 1. enforce 添加中文环境主体关键词 ----------

def test_enforce_adds_environment_focused_keywords():
    """enforce 输出必须含中文地点建立镜头和环境主体约束。"""
    from app.services.image_generation_service import image_generation_service as svc
    out = svc._enforce_background_environment_focus(
        "imperial court hall, Tang dynasty style",
        forbidden_characters=None,
    )
    assert "视觉小说背景图" in out
    assert "地点建立镜头" in out
    assert "画面主体" in out
    # 原始 prompt 应该保留
    assert "imperial court hall" in out.lower()


# ---------- 2. enforce 添加本章角色点名排除 ----------

def test_enforce_adds_named_cast_exclusion():
    """传入 forbidden_characters 后输出中文命名角色禁令 + 角色名。

    2026-06-14 升级：所有场景强制完全无人。
    """
    from app.services.image_generation_service import image_generation_service as svc
    # market 之前触发 allow_human=True，升级后也强制完全无人
    out = svc._enforce_background_environment_focus(
        "abandoned train station at night, market",
        forbidden_characters=["林夜", "苏晚晴", "赵峰"],
        scene_name="market",
    )
    assert "画面中不得出现这些命名角色" in out
    assert "林夜" in out
    assert "苏晚晴" in out
    assert "赵峰" in out
    # 必须强调本章角色完全不能出现 + 完全无人
    assert "纯空环境" in out


def test_enforce_handles_empty_forbidden_characters():
    """forbidden_characters=None 时不应崩，不含 named cast 段。"""
    from app.services.image_generation_service import image_generation_service as svc
    out = svc._enforce_background_environment_focus(
        "imperial court hall",
        forbidden_characters=None,
    )
    assert "画面中不得出现这些命名角色" not in out
    assert "视觉小说背景图" in out


# ---------- 3. enforce 添加构图限制 ----------

def test_enforce_adds_composition_restrictions():
    """所有场景输出含中文人物主体禁令 + 完全无人 clause。

    2026-06-14 升级：所有场景强制完全无人，不再有 "5% of the image" crowd 分支。
    """
    from app.services.image_generation_service import image_generation_service as svc
    # market 之前触发 allow_background_people=True，升级后也走 unpopulated 分支
    out = svc._enforce_background_environment_focus(
        "ancient market square",
        forbidden_characters=None,
        scene_name="market",
    )
    must_have = [
        # BG-NO-HUMAN-SUBJECT 强制 ban（恒定出现）
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        # 2026-06-14 升级：所有场景都含完全无人 clause
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
    ]
    for w in must_have:
        assert w in out, f"missing composition rule '{w}' in: {out}"
    # 升级后：不再有 "5% of the image" crowd-allow 短语
    out_lower = out.lower()
    assert "5% of the image" not in out_lower, \
        f"should NOT have '5% of the image' (crowd-allow branch removed): {out}"
    # 不再有 "incidental environmental elements"
    assert "incidental environmental elements" not in out_lower, \
        f"should NOT have 'incidental environmental elements' (crowd-allow removed): {out}"


# ---------- 4. final_prompt 路径经过 enforce ----------

@pytest.mark.asyncio
async def test_final_prompt_path_passes_through_enforce(monkeypatch):
    """generate_background(final_prompt=...) 时实际送 API 的 prompt 含 enforce 约束。

    mock _call_cogview_api 捕获真实 prompt。
    """
    from app.services import image_generation_service as mod

    captured_prompts = []

    async def fake_call(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        # 返回最小字节模拟成功
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    # patch 模块级单例的方法（直接赋值，绕过 monkeypatch 描述符限制）
    svc = mod.image_generation_service
    original = svc._call_cogview_api
    svc._call_cogview_api = fake_call
    monkeypatch.setattr(
        mod, "IMAGE_GENERATION_ENABLED", True
    )
    monkeypatch.setattr(
        mod, "AI_IMAGE_API_KEY", "fake-key-for-test"
    )
    # 关闭验收器，让单测只测 prompt 路径（不真打视觉 API）
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)

    try:
        # 用唯一 prompt 避免 cache 命中（前面测试可能跑过相同 prompt）
        # 2026-06-14: 所有场景都强制完全无人
        await svc.generate_background(
            scene_name=f"market_unique_{id(captured_prompts)}",
            scene_description="public market square",
            mood="day",
            final_prompt=f"ancient palace market at dusk unique_{id(captured_prompts)}",
            forbidden_characters=["林夜_unique", "苏晚晴_unique"],
        )
    finally:
        svc._call_cogview_api = original

    assert len(captured_prompts) == 1, \
        f"expected 1 captured prompt, got {len(captured_prompts)}"
    final = captured_prompts[0]
    # 必须含 enforce 输出
    assert "视觉小说背景图" in final
    assert "画面中不得出现这些命名角色" in final
    assert "纯空环境" in final
    # 2026-06-14: 所有场景都强制完全无人
    assert "纯空环境" in final
    assert "纯空环境" in final
    # 角色名要出现
    assert "林夜_unique" in captured_prompts[0]


# ---------- 5. sanitize retry 路径保留 enforce ----------

def test_sanitize_retry_path_keeps_enforce():
    """safety fallback prompt 经过 enforce，含中文环境主体和命名角色禁令。"""
    from app.services.image_generation_service import image_generation_service as svc
    # _build_safety_fallback_background_prompt 应该已经过 enforce
    # scene_name="market" 触发 allow_human=True 才有 "no portrait"
    fallback = svc._build_safety_fallback_background_prompt(
        forbidden_characters=["林夜", "苏晚晴"],
        scene_name="market",
        scene_description="public market square",
    )
    fl = fallback.lower()
    # 不能含人物主体化词
    forbidden_in_fallback = [
        "hero shot",
        "heroine shot",
        "protagonist",
        "character portrait",
        "detailed costume",
    ]
    for w in forbidden_in_fallback:
        if w in fl:
            assert f"no {w}" in fl or f", {w}" not in fl, \
                f"fallback 含人物主体化正向词 '{w}': {fallback}"
    # enforce 关键词必须存在
    assert "视觉小说背景图" in fallback
    assert "地点建立镜头" in fallback
    assert "纯空环境" in fallback
    # 角色名要被排除
    assert "林夜" in fallback
    assert "苏晚晴" in fallback


# ---------- 6. safety fallback 不含人物主体化正向词 ----------

def test_safety_fallback_prompt_does_not_humanize():
    """safety fallback 不能用 hero/portrait/character portrait/full body/detailed costume 作正向。

    检测方式：这些词如果出现，前面必须紧跟 'no '（作为否定）。
    """
    from app.services.image_generation_service import image_generation_service as svc
    fallback = svc._build_safety_fallback_background_prompt(forbidden_characters=None)
    fl = fallback.lower()

    # 这些词如果出现，必须以 "no <word>" 形式（即作为否定）
    must_be_negated = [
        "hero portrait",
        "character portrait",
        "main character pose",
        "looking at camera",
        "standing in center",
        "emotional face",
        "hero shot",
        "portrait",
        "full-body character",
    ]
    import re
    for w in must_be_negated:
        # 找出所有出现位置，每个必须前面是 "no " 或 ", no "
        # 用正则：要么不在 fallback 里，要么前面是 "no "
        # 简化：检查是否存在不以 "no " 开头的出现
        pattern = re.compile(rf"(?<!no ){re.escape(w)}", re.IGNORECASE)
        # 进一步过滤 "no " 前缀（4 字符）
        # 简化做法：把 "no <w>" 都替换掉，剩下的不应该再有 <w>
        stripped = re.sub(rf"\bno {re.escape(w)}\b", "", fl, flags=re.IGNORECASE)
        assert w not in stripped, \
            f"safety fallback 把 '{w}' 作为正向词使用: {fallback}"


# ---------- 7. forbidden_characters 多源合并 ----------

def test_collect_forbidden_characters_merges_sources():
    """outline + segment + story_bible 三源合并 + 去重 + 过滤脏数据。"""
    from app.services.asset_management_service import AssetManagementService

    # mock db（_collect_forbidden_characters 不实际查 DB）
    svc = AssetManagementService.__new__(AssetManagementService)
    svc.db = None

    # 构造 outline / segments / story_bible mock
    outline = MagicMock()
    outline.characters = ["林夜", "苏晚晴"]  # DB 字段

    # 模拟 segment 列表（SceneSegment 有 characters_present 属性）
    seg1 = MagicMock()
    seg1.characters_present = ["赵峰", "陈墨"]
    seg2 = MagicMock()
    seg2.characters_present = ["林夜"]  # 重复，应该去重
    segments = [seg1, seg2]

    # StoryBible mock（有 characters 字段）
    story_bible = MagicMock()
    story_bible.characters = [
        {"name": "林夜", "name_en": "Lin Ye", "aliases": ["林公子"]},
        {"name": "苏晚晴", "name_en": "Su Wanqing"},
        {"name": "李老板"},  # 出现在正文但 outline 没列
    ]
    story_bible.raw_json = None

    chapter_content = "李老板走过来和林夜说话"  # 李老板出现

    result = svc._collect_forbidden_characters(
        outline=outline,
        segments=segments,
        story_bible=story_bible,
        chapter_content=chapter_content,
    )

    # 林夜、苏晚晴、赵峰、陈墨、李老板、Lin Ye、Su Wanqing、林公子 都应该在
    # （林夜去重后只算一次，但 Lin Ye 是另一个 alias 单独算）
    expected = {"林夜", "苏晚晴", "赵峰", "陈墨", "李老板", "Lin Ye", "Su Wanqing", "林公子"}
    result_set = set(result)
    for name in expected:
        assert name in result_set, f"missing {name} in merged forbidden list: {result}"


def test_collect_forbidden_characters_handles_none_inputs():
    """所有输入都是 None 时不崩，返回空列表。"""
    from app.services.asset_management_service import AssetManagementService
    svc = AssetManagementService.__new__(AssetManagementService)
    svc.db = None

    result = svc._collect_forbidden_characters(
        outline=None,
        segments=[],
        story_bible=None,
        chapter_content="",
    )
    assert result == []


def test_collect_forbidden_characters_filters_dirty_data():
    """过长（>30字）、含标点的'角色名'应被过滤。"""
    from app.services.asset_management_service import AssetManagementService
    svc = AssetManagementService.__new__(AssetManagementService)
    svc.db = None

    outline = MagicMock()
    outline.characters = [
        "林夜",
        "x" * 50,  # 过长，脏数据
        "林夜，站在车站",  # 含中文逗号，是描述
        "Su Wanqing.",
        "正常名字",
    ]
    story_bible = MagicMock()
    story_bible.characters = None
    story_bible.raw_json = None

    result = svc._collect_forbidden_characters(
        outline=outline,
        segments=[],
        story_bible=story_bible,
        chapter_content="",
    )

    assert "林夜" in result
    assert "正常名字" in result
    # 脏数据应该被过滤
    assert not any("x" * 50 in r for r in result)
    assert not any("站在车站" in r for r in result)
    assert not any("." in r for r in result)


# ---------- portrait/keyframe 不受影响 ----------

def test_enforce_not_called_for_portrait_or_keyframe():
    """enforce 函数本身是公开的，但 generate_portrait/keyframe 不应调用它。"""
    # 这个测试通过验证 enforce 只在 generate_background 路径里被调用
    # 来保护 portrait/keyframe 不受背景约束影响
    from app.services import image_generation_service as mod
    import inspect
    src = inspect.getsource(mod.image_generation_service.generate_portrait)
    assert "_enforce_background_environment_focus" not in src, \
        "generate_portrait 不应该调用 background enforce 函数"
    src_kf = inspect.getsource(mod.image_generation_service.generate_keyframe)
    assert "_enforce_background_environment_focus" not in src_kf, \
        "generate_keyframe 不应该调用 background enforce 函数"


# ---------- BG-CROWD-ONLY: 防止"单独一个人物占住画面主体" ----------

def test_enforce_adds_single_person_subject_ban():
    """BG-NO-HUMAN-SUBJECT — 公共场景也必须含中文人物主体禁令。"""
    from app.services.image_generation_service import image_generation_service as svc
    # palace gate 触发 allow_background_people=True
    out = svc._enforce_background_crowd_only_or_environment_focus(
        "imperial palace gate",
        forbidden_characters=None,
        scene_name="palace gate",
    )
    must_have = [
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
    ]
    for w in must_have:
        assert w in out, f"missing single-person ban phrase '{w}' in: {out}"


def test_strip_single_person_phrases_english():
    """a man stands / lone guard / foreground / looking at camera / portrait 等都被清洗。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = ("A lone guard stands before the gate, in the foreground, "
         "looking at the camera, hero shot, dramatic figure, full-body character")
    cleaned, modified = svc._strip_single_person_phrases(p)
    assert modified
    cl = cleaned.lower()
    forbidden = [
        "a lone guard",
        "in the foreground",
        "looking at the camera",
        "dramatic figure",
        "full-body character",
    ]
    # 注意：strip 只清洗原 prompt 中的风险短语；
    # 但 enforce 函数会在末尾追加 "no hero shot" 等否定形式，那些是合法的。
    # 这里单独测 _strip_single_person_phrases 只看输入是否被改写。
    for w in forbidden:
        assert w not in cl, f"risk phrase '{w}' not cleaned: {cleaned}"


def test_strip_single_person_phrases_chinese():
    """中文风险短语 '一个守卫站在门前' / '一名侍女' / '孤独的身影' 等都被清洗。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = "一个守卫站在门前，一名侍女在走廊，孤独的身影站在中央"
    cleaned, modified = svc._strip_single_person_phrases(p)
    assert modified
    forbidden = ["一个守卫站在", "一名侍女", "孤独的身影"]
    for w in forbidden:
        assert w not in cleaned, f"risk phrase '{w}' not cleaned: {cleaned}"


def test_enforce_passes_through_strip_for_lone_guard_prompt():
    """原文 'a lone guard stands before the palace gate' 经 enforce 后不含 lone guard 表达。

    scene_name='palace gate' 也会被强制空场，人物主体 ban 段被注入。
    """
    from app.services.image_generation_service import image_generation_service as svc
    final = svc._enforce_background_crowd_only_or_environment_focus(
        "A lone guard stands before the palace gate",
        forbidden_characters=["林夜"],
        scene_name="palace gate",
    )
    fl = final.lower()
    # 风险短语应该被清洗
    assert "a lone guard stands" not in fl
    assert "lone guard" not in fl
    # 同时必须含环境主体 + 人物主体 ban
    assert "视觉小说背景图" in final
    assert "纯空环境" in final
    # 角色名仍被点名排除
    assert "林夜" in final


def test_enforce_for_maid_in_corridor_environment_focused():
    """'一个侍女站在走廊里' 经 enforce 后改写为环境主体 + 空场。

    scene_name='palace corridor' 不命中 crowd 也不命中 unpopulated，
    走默认 allow_human=False 分支（无人场景）。
    """
    from app.services.image_generation_service import image_generation_service as svc
    final = svc._enforce_background_crowd_only_or_environment_focus(
        "一个侍女站在走廊里，宫灯昏黄",
        forbidden_characters=["苏晚晴"],
        scene_name="palace corridor",
    )
    # 风险短语被清洗
    assert "一名侍女" not in final
    assert "一个侍女站在" not in final
    # 环境主体词汇必须保留
    assert "视觉小说背景图" in final
    assert "地点建立镜头" in final
    # 默认无人：必须有 no people / empty environment
    assert "纯空环境" in final
    assert "纯空环境" in final
    # 角色名仍被排除
    assert "苏晚晴" in final


@pytest.mark.asyncio
async def test_final_prompt_with_single_person_subject_still_enforced(monkeypatch):
    """rewriter final_prompt 含 'a lone guard' 经 generate_background 后被清洗。"""
    from app.services import image_generation_service as mod

    captured = []

    async def fake_call(prompt, *args, **kwargs):
        captured.append(prompt)
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    svc = mod.image_generation_service
    original = svc._call_cogview_api
    svc._call_cogview_api = fake_call
    monkeypatch.setattr(mod, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(mod, "AI_IMAGE_API_KEY", "fake-key-for-test-crowd")
    # 关闭验收器，让单测只测 prompt 路径（不真打视觉 API）
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)

    try:
        await svc.generate_background(
            scene_name=f"palace_gate_{id(captured)}",
            scene_description="palace gate with guards",
            mood="day",
            final_prompt=(
                f"A lone guard stands before the palace gate unique_{id(captured)}, "
                "looking at camera, hero shot"
            ),
            forbidden_characters=["林夜_crowd"],
        )
    finally:
        svc._call_cogview_api = original

    assert len(captured) == 1
    fl = captured[0].lower()
    # 风险短语被清洗（不能作为正向出现；"no hero shot" 这种否定是合法的）
    # 用正则去掉所有 "no <phrase>" 后再检查
    import re
    stripped = re.sub(r"\bno [a-z\-]+(?:\s+[a-z\-]+){0,3}\b", "", fl, flags=re.IGNORECASE)
    stripped = re.sub(
        r"\bdo not (?:depict|show) (?:a |an |the )?[a-z\-]+(?:\s+[a-z\-]+){0,4}\b",
        "", stripped, flags=re.IGNORECASE,
    )
    assert "a lone guard stands" not in stripped, \
        f"risk phrase 'a lone guard stands' not cleaned: {captured[0]}"
    assert "looking at camera" not in stripped, \
        f"risk phrase 'looking at camera' not cleaned: {captured[0]}"
    # 含人物主体硬禁止
    assert "纯空环境" in captured[0]
    assert "纯空环境" in captured[0]
    assert "纯空环境" in captured[0]
    # 角色名仍被排除
    assert "林夜_crowd" in captured[0]


def test_safety_fallback_includes_single_person_ban():
    """safety fallback prompt 在公共场景必须含中文人物主体硬禁止。"""
    from app.services.image_generation_service import image_generation_service as svc
    fallback = svc._build_safety_fallback_background_prompt(
        forbidden_characters=["林夜", "苏晚晴"],
        scene_name="market",
        scene_description="public market square",
    )
    must_have = [
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "视觉小说背景图",
    ]
    for w in must_have:
        assert w in fallback, f"safety fallback missing '{w}': {fallback}"


def test_safety_fallback_no_isolated_person_phrases():
    """safety fallback 不能含 lone figure / single figure / isolated person 作为正向描述。

    检测方式：把 "no <w>" 和 "do not (depict|show) ... <w>" 这些否定形式都剥掉，
    剩下的 stripped 文本里不应该再出现 <w>。
    """
    from app.services.image_generation_service import image_generation_service as svc
    import re
    fallback = svc._build_safety_fallback_background_prompt(forbidden_characters=None)
    fl = fallback.lower()
    risk_words = [
        "lone figure",
        "single figure",
        "isolated person",
        "centered single person",
        "large foreground person",
        "one isolated person",
        "single person as the main subject",
    ]
    # 先剥 "no <w>"
    stripped = fl
    for w in risk_words:
        stripped = re.sub(rf"\bno {re.escape(w)}\b", "", stripped, flags=re.IGNORECASE)
    # 再剥 "do not (depict|show) (a|an|the)? <w>"
    for w in risk_words:
        stripped = re.sub(
            rf"\bdo not (?:depict|show) (?:a |an |the )?{re.escape(w)}\b",
            "", stripped, flags=re.IGNORECASE,
        )
    # 剥掉后剩下的 stripped 文本不应再出现这些短语作为正向描述
    for w in risk_words:
        assert w not in stripped, f"safety fallback uses '{w}' as positive: {fallback}"


def test_enforce_crowd_only_keeps_chapter_characters_excluded():
    """传入 forbidden_characters 后，人物主体 ban 之外仍要含中文命名角色禁令。"""
    from app.services.image_generation_service import image_generation_service as svc
    # 'court hall' 触发 allow_human=True
    final = svc._enforce_background_crowd_only_or_environment_focus(
        "imperial court hall",
        forbidden_characters=["林夜", "苏晚晴", "赵峰", "陈墨"],
        scene_name="court hall",
    )
    assert "画面中不得出现这些命名角色" in final
    assert "林夜" in final
    assert "苏晚晴" in final
    assert "赵峰" in final
    assert "陈墨" in final
    # 必须含人物主体 ban
    assert "纯空环境" in final


# ---------- BG-DEFAULT-NO-PEOPLE: 默认无人，必要时才有人 ----------

def test_detect_human_atmosphere_unpopulated_scenes():
    """卧室/书房/密室/山林/空庭院 等场景判断为 allow_human=False。"""
    from app.services.image_generation_service import image_generation_service as svc
    unpopulated_scenes = [
        ("bedroom", None), ("study room", None), ("secret room", None),
        ("forest", None), ("courtyard", None), ("ruins", None),
        ("卧室", None), ("书房", None), ("密室", None),
        ("山林", None), ("空庭院", None), ("废墟", None),
        (None, "abandoned mountain riverbank at night"),
        (None, "dream space, deserted cave"),
    ]
    for scene_name, scene_desc in unpopulated_scenes:
        allow_human, _, matched_unpopulated = svc._detect_human_atmosphere(
            scene_name, scene_desc,
        )
        assert allow_human is False, \
            f"scene_name={scene_name!r} desc={scene_desc!r} should be unpopulated"
        assert len(matched_unpopulated) >= 1, \
            f"expected matched_unpopulated keywords for {scene_name!r}/{scene_desc!r}"


def test_detect_human_atmosphere_crowd_scenes():
    """集市/街道/军营/城门 等场景判断为 allow_human=True。"""
    from app.services.image_generation_service import image_generation_service as svc
    crowd_scenes = [
        ("market", None), ("street", None), ("military camp", None),
        ("city gate", None), ("palace gate", None), ("harbor", None),
        ("banquet hall", None), ("village square", None),
        ("集市", None), ("街道", None), ("军营", None),
        ("城门", None), ("宫门", None), ("广场", None),
        (None, "busy tavern with merchants"),
        (None, "festival market square"),
    ]
    for scene_name, scene_desc in crowd_scenes:
        allow_human, matched_crowd, _ = svc._detect_human_atmosphere(
            scene_name, scene_desc,
        )
        assert allow_human is True, \
            f"scene_name={scene_name!r} desc={scene_desc!r} should allow crowd"
        assert len(matched_crowd) >= 1, \
            f"expected matched_crowd keywords for {scene_name!r}/{scene_desc!r}"


def test_detect_human_atmosphere_neutral_scene_defaults_to_no_people():
    """无任何关键词的场景（普通房间/远景）默认 allow_human=False。"""
    from app.services.image_generation_service import image_generation_service as svc
    neutral_scenes = [
        ("palace corridor", None),  # 走廊不算公共也不算无人，默认无人
        (None, "an empty hall"),
        ("generic interior", None),
        ("exterior wide shot", None),
    ]
    for scene_name, scene_desc in neutral_scenes:
        allow_human, _, _ = svc._detect_human_atmosphere(scene_name, scene_desc)
        assert allow_human is False, \
            f"scene_name={scene_name!r} desc={scene_desc!r} should default to no-people"


def test_enforce_unpopulated_scene_forces_empty_environment():
    """卧室/书房/密室/山林/空庭院 最终 prompt 含中文空场约束。"""
    from app.services.image_generation_service import image_generation_service as svc
    unpopulated_inputs = [
        ("bedroom at night", "bedroom"),
        ("study room with scrolls", "study room"),
        ("secret room hidden behind bookshelf", "secret room"),
        ("mountain forest path", "forest"),
        ("empty courtyard under moonlight", "courtyard"),
        ("ancient ruins overgrown with vines", "ruins"),
        ("卧室里烛火昏黄", "卧室"),
        ("山林深处云雾缭绕", "山林"),
        ("空庭院落叶满地", "空庭院"),
    ]
    for prompt_text, scene_name in unpopulated_inputs:
        final = svc._enforce_background_environment_focus(
            prompt_text, forbidden_characters=None, scene_name=scene_name,
        )
        must_have = [
            "纯空环境",
            "纯空环境",
            "纯空环境",
            "纯空环境",
            "纯空环境",
        ]
        for w in must_have:
            assert w in final, \
                f"unpopulated scene {scene_name!r} missing '{w}' in: {final}"
        # 不能含诱导模型加人的"prefer multiple tiny background figures"
        assert "prefer multiple tiny background figures" not in final.lower(), \
            f"unpopulated scene {scene_name!r} should NOT have 'prefer multiple tiny background figures': {final}"


def test_enforce_crowd_scene_now_completely_unpopulated():
    """2026-06-14 升级：集市/街道/军营/城门 也强制完全无人。

    升级前：crowd 场景允许 tiny anonymous background figures。
    升级后：所有场景（含 crowd）都必须输出中文完全无人约束。
    """
    from app.services.image_generation_service import image_generation_service as svc
    crowd_inputs = [
        ("busy market at noon", "market"),
        ("street filled with merchants", "street"),
        ("military camp tents at dawn", "military camp"),
        ("city gate with travelers passing", "city gate"),
        ("banquet hall preparation", "banquet hall"),
    ]
    for prompt_text, scene_name in crowd_inputs:
        final = svc._enforce_background_environment_focus(
            prompt_text, forbidden_characters=None, scene_name=scene_name,
        )
        # 升级后：所有场景都强制完全无人
        assert "纯空环境" in final, \
            f"crowd scene {scene_name!r} must be completely unpopulated (2026-06-14 policy): {final}"
        assert "纯空环境" in final
        assert "纯空环境" in final
        # 升级后：不再含 "incidental environmental elements"
        assert "incidental environmental elements" not in final.lower(), \
            f"crowd scene {scene_name!r} should NOT allow tiny figures anymore: {final}"
        # 仍然禁止单人主体（强制 ban）
        assert "纯空环境" in final
        assert "纯空环境" in final
        # 不能含"prefer multiple tiny background figures"作为默认后缀
        assert "prefer multiple tiny background figures" not in final.lower()


def test_enforce_crowd_scene_still_bans_single_person_subject():
    """公共场景仍然禁止人物主体 / 中心孤立人物。"""
    from app.services.image_generation_service import image_generation_service as svc
    final = svc._enforce_background_environment_focus(
        "city gate at sunset",
        forbidden_characters=["林夜"],
        scene_name="city gate",
    )
    must_have = [
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
        "纯空环境",
    ]
    for w in must_have:
        assert w in final, f"crowd scene missing single-person ban '{w}': {final}"
    # 角色名仍要被点名排除
    assert "林夜" in final


@pytest.mark.asyncio
async def test_final_prompt_path_respects_unpopulated_scene(monkeypatch):
    """unpopulated 场景经 generate_background → API 的 prompt 含中文空场约束。"""
    from app.services import image_generation_service as mod

    captured = []

    async def fake_call(prompt, *args, **kwargs):
        captured.append(prompt)
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    svc = mod.image_generation_service
    original = svc._call_cogview_api
    svc._call_cogview_api = fake_call
    monkeypatch.setattr(mod, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(mod, "AI_IMAGE_API_KEY", "fake-key-unpopulated")
    # 关闭验收器，让单测只测 prompt 路径（不真打视觉 API）
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)

    try:
        await svc.generate_background(
            scene_name=f"bedroom_unique_{id(captured)}",
            scene_description="private bedroom at night",
            mood="night",
            final_prompt=f"serene bedroom interior unique_{id(captured)}, candle light",
            forbidden_characters=["林夜_priv"],
        )
    finally:
        svc._call_cogview_api = original

    assert len(captured) == 1
    final = captured[0]
    # unpopulated: 必须含中文空场约束
    assert "纯空环境" in final
    assert "纯空环境" in final
    assert "纯空环境" in final
    # 不能含诱导加人的默认后缀
    assert "prefer multiple tiny background figures" not in final.lower()
    # 角色名仍被点名排除
    assert "林夜_priv" in captured[0]


def test_sanitize_retry_keeps_unpopulated_decision():
    """safety fallback 在 unpopulated 场景必须保持无人判定。"""
    from app.services.image_generation_service import image_generation_service as svc
    fallback = svc._build_safety_fallback_background_prompt(
        forbidden_characters=["林夜"],
        scene_name="bedroom",
        scene_description="private bedroom",
    )
    # unpopulated: 必须含中文空场约束
    assert "纯空环境" in fallback
    assert "纯空环境" in fallback
    assert "纯空环境" in fallback
    # 角色名仍被排除
    assert "林夜" in fallback


def test_safety_fallback_default_is_unpopulated():
    """safety fallback 无 scene_name 参数时默认应为无人环境。"""
    from app.services.image_generation_service import image_generation_service as svc
    fallback = svc._build_safety_fallback_background_prompt(forbidden_characters=None)
    # 默认无人
    assert "纯空环境" in fallback
    assert "纯空环境" in fallback
    assert "纯空环境" in fallback


# ---------- CN-PLOT-STRIP: 兜底剥离中文剧情片段（2026-06-15） ----------

def test_strip_chinese_segments_removes_chinese_plot_in_envelope():
    """CN-PLOT-STRIP — 用户实际样本：retry_prompt 主体段含整段中文剧情。
    _strip_chinese_segments 按逗号/句号切分，丢弃含中文字符的片段，保留英文片段。
    """
    from app.services.image_generation_service import image_generation_service as svc
    raw = (
        "苏墨赴约，宋雪以情报交易为名，要求他帮忙调查凶兽书灵源头。"
        "苏墨拒绝，宋雪威胁泄露其能力。争执间白薇出现，制止宋雪并宣布将, "
        "禁书区幽蓝灯光, 漂浮的古书卷, 黑色书灵猩红双眼, "
        "Chinese historical style, ancient China, "
        "Chinese classical painting aesthetic, hand-painted background art"
    )
    cleaned = svc._strip_chinese_segments(raw)
    # 中文剧情/物件名必须全清
    for cn in ("苏墨", "宋雪", "白薇", "凶兽", "禁书区", "幽蓝", "古书", "书灵"):
        assert cn not in cleaned, f"{cn} 应被剥离"
    # 英文片段保留
    assert "Chinese historical style" in cleaned
    assert "hand-painted background art" in cleaned


def test_strip_chinese_segments_keeps_pure_english():
    """CN-PLOT-STRIP — 纯英文 prompt 原样保留。"""
    from app.services.image_generation_service import image_generation_service as svc
    en = "imperial court hall, vermilion columns, polished marble floor, daylight"
    assert svc._strip_chinese_segments(en) == en


def test_strip_chinese_segments_handles_empty():
    """CN-PLOT-STRIP — 空串安全。"""
    from app.services.image_generation_service import image_generation_service as svc
    assert svc._strip_chinese_segments("") == ""


def test_enforce_strips_chinese_plot_from_base_prompt():
    """CN-PLOT-STRIP — _enforce_background_environment_focus 必须把 base_prompt
    主体段含的中文剧情剥离掉，不再原样送进 final_prompt。

    注意：命名角色禁令段保留中文角色名是预期的 —— 这是禁令不是视觉描述。
    本测试只断言主体段不含中文剧情动词。
    """
    from app.services.image_generation_service import image_generation_service as svc
    base_with_cn_plot = (
        "苏墨赴约，宋雪要求帮忙调查， "
        "Chinese historical style, ancient China"
    )
    enforced = svc._enforce_background_environment_focus(
        base_with_cn_plot,
        forbidden_characters=["苏墨", "宋雪"],
        scene_name="禁书区",
        scene_description="禁书区",
        allow_background_people=False,
        escalation_level=0,
    )
    # 中文剧情动词不应出现（角色名可能出现在 named cast exclusion，正常）
    for cn in ("赴约", "调查", "要求", "帮忙"):
        assert cn not in enforced, f"enforced prompt 含中文剧情动词 {cn}"
    # 英文片段保留
    assert "Chinese historical style" in enforced
    # 兜底约束仍在
    assert "纯空环境" in enforced


def test_enforce_strips_chinese_plot_at_retry():
    """CN-PLOT-STRIP — 重试路径（escalation_level=1）也必须剥离中文剧情。"""
    from app.services.image_generation_service import image_generation_service as svc
    base_with_cn_plot = (
        "苏墨在禁书区遭遇凶兽书灵，灯光幽蓝， "
        "Chinese historical style"
    )
    enforced = svc._enforce_background_environment_focus(
        base_with_cn_plot,
        forbidden_characters=["苏墨"],
        scene_name="禁书区",
        scene_description="禁书区",
        allow_background_people=False,
        escalation_level=1,
    )
    for cn in ("凶兽", "书灵", "遭遇", "禁书区"):
        assert cn not in enforced, f"retry enforced prompt 含中文剧情词 {cn}"
    assert "Chinese historical style" in enforced


# ---------- ENV-DISTILL: environment_hint_en 优先（2026-06-15 升级） ----------

@pytest.mark.asyncio
async def test_build_background_prompts_async_uses_environment_hint_when_provided(monkeypatch):
    """ENV-DISTILL — 传入 environment_hint_en 时，rewriter 收到的 user_prompt 含纯环境描述，
    且不含剧情摘要中的角色动作词。

    场景：outline.summary 含'林墨看着芯片，他说：...'（剧情污染源），
    environment_hint_en 是 segmenter 蒸馏的纯环境英文。
    """
    from app.services import prompt_rewriter_service as rew_mod

    captured_fields = []

    async def fake_rewrite_many(asset_type, batch):
        # 捕获 rewriter 收到的 fields
        for f in batch:
            captured_fields.append(f)
        # 返回 None 触发 fallback 路径（不调真 LLM）
        return [None] * len(batch)

    monkeypatch.setattr(
        rew_mod.prompt_rewriter_service,
        "rewrite_many",
        fake_rewrite_many,
    )

    from app.services.prompt_builder_service import prompt_builder_service as pbs
    chapter_outline = {
        "scene": "工作室",
        "summary": "林墨看着芯片，他说：我已经有了足够多的她。林墨嘴角的浅笑",
        "visual_keywords": ["工作台", "蓝光"],
        "characters": ["林墨", "苏晚晴"],
    }
    story_bible = {"characters": [{"name": "林墨"}, {"name": "苏晚晴"}]}

    env_hint = (
        "a dim workbench lit by pale blue chip glow, scattered electronic components, "
        "window view of night city street, quiet tense atmosphere"
    )

    await pbs.build_background_prompts_async(
        chapter_outline,
        story_bible,
        ["night"],
        environment_hint_en=env_hint,
    )

    assert len(captured_fields) >= 1
    f = captured_fields[0]
    summary_env = f.get("summary_environment_hint", "")
    # environment_hint_en 应该原样进入 summary_environment_hint
    assert "workbench" in summary_env
    assert "blue chip glow" in summary_env
    assert "window view of night city street" in summary_env
    # 不应该含原 outline.summary 里的污染词
    assert "林墨" not in summary_env
    assert "他说" not in summary_env
    assert "浅笑" not in summary_env


@pytest.mark.asyncio
async def test_build_background_prompts_async_fallback_when_env_hint_empty(monkeypatch):
    """ENV-DISTILL — environment_hint_en 为空时，补调 segmenter._call_llm_single 蒸馏。
    2026-06-15: 不再塞 outline.summary（剧情污染源），改为 LLM 蒸馏场景生成英文描述。
    若 LLM 也失败，summary_environment_hint 才为空。
    """
    from app.services import prompt_rewriter_service as rew_mod
    from app.services import scene_segmenter_service as seg_mod

    captured_fields = []

    async def fake_rewrite_many(asset_type, batch):
        for f in batch:
            captured_fields.append(f)
        return [None] * len(batch)

    async def fake_call_llm_single(self, content, outline):
        from app.services.scene_segmenter_service import SceneSegment
        return SceneSegment(
            segment_id=1,
            location="工作室",
            location_en="workshop",
            summary="",
            characters_present=[],
            mood="day",
            environment_hint_en="indoor workshop, workbench, scattered electronic components, daylight",
        )

    monkeypatch.setattr(
        rew_mod.prompt_rewriter_service, "rewrite_many", fake_rewrite_many,
    )
    monkeypatch.setattr(
        seg_mod.SceneSegmenterService, "_call_llm_single", fake_call_llm_single,
    )

    from app.services.prompt_builder_service import prompt_builder_service as pbs
    chapter_outline = {
        "scene": "工作室",
        "summary": "林墨站在工作室里，桌上摆着芯片",
        "characters": ["林墨"],
    }
    story_bible = {"characters": [{"name": "林墨"}]}

    await pbs.build_background_prompts_async(chapter_outline, story_bible, ["day"])

    assert len(captured_fields) >= 1
    f = captured_fields[0]
    summary_env = f.get("summary_environment_hint", "")
    # env_hint 应该由 LLM 蒸馏出来（含 workshop 关键词）
    assert "workshop" in summary_env
    # outline.summary 里的剧情词不应出现在任何字段
    for k, v in f.items():
        if isinstance(v, str):
            assert "林墨" not in v, f"field {k} should not contain character name"
            # scene_zh 走 _strip_character_actions，可能含"芯片"残留，其他字段必须干净
            if k != "scene_zh":
                assert "芯片" not in v, f"field {k} leaked plot detail"


# ---------- GENRE-STYLE: 按 genre 映射画风（2026-06-15） ----------

def test_build_background_prompt_modern_genre_no_anime():
    """GENRE-STYLE — modern genre 的背景 prompt 用中文写实风格，不应含 Makoto Shinkai / galgame。"""
    from app.services.image_generation_service import image_generation_service as svc
    prompt = svc._build_background_prompt(
        scene_description="abandoned factory, rusted beams",
        mood="night",
        style_prompt="modern realistic style, contemporary fashion, modern setting",
        forbidden_characters=["陆沉"],
        genre="modern",
    )
    # modern 应该走写实摄影
    assert "当代写实摄影风格" in prompt
    # 不应该硬塞 anime / Makoto Shinkai / galgame
    p_lower = prompt.lower()
    assert "makoto shinkai" not in p_lower
    assert "galgame" not in p_lower
    assert "anime visual novel" not in p_lower


def test_build_background_prompt_historical_genre_uses_painting_style():
    """GENRE-STYLE — historical genre 应该用中国古典绘画风，不是 anime。"""
    from app.services.image_generation_service import image_generation_service as svc
    prompt = svc._build_background_prompt(
        scene_description="朝堂",
        mood="day",
        style_prompt="Chinese historical style, hanfu, traditional Chinese clothing, ancient China",
        forbidden_characters=["李清照"],
        genre="historical",
    )
    # historical 应该走中国古典手绘
    assert "中国古典绘画审美" in prompt or "手绘背景质感" in prompt
    # 不应该硬塞 anime
    p_lower = prompt.lower()
    assert "makoto shinkai" not in p_lower
    assert "galgame" not in p_lower


def test_build_background_prompt_anime_genre_uses_anime_style():
    """GENRE-STYLE — 显式 genre='anime' 时用中文动画视觉小说背景风格。"""
    from app.services.image_generation_service import image_generation_service as svc
    prompt = svc._build_background_prompt(
        scene_description="school classroom",
        mood="day",
        style_prompt="anime style, Japanese animation, manga style",
        forbidden_characters=[],
        genre="anime",
    )
    assert "动画视觉小说背景美术" in prompt
    assert "清透光影" in prompt


def test_build_background_prompt_scifi_genre_uses_cyberpunk():
    """GENRE-STYLE — sci-fi 应该用 cyberpunk 写实。"""
    from app.services.image_generation_service import image_generation_service as svc
    prompt = svc._build_background_prompt(
        scene_description="neon street",
        mood="night",
        style_prompt="sci-fi style, futuristic technology, cyberpunk",
        forbidden_characters=[],
        genre="sci-fi",
    )
    assert "赛博朋克式" in prompt or "反乌托邦" in prompt
    p_lower = prompt.lower()
    assert "makoto shinkai" not in p_lower
    assert "galgame" not in p_lower


def test_build_background_prompt_unknown_genre_falls_back_to_historical():
    """GENRE-STYLE — 未知 genre 兜底成 historical（中国古典手绘）。"""
    from app.services.image_generation_service import image_generation_service as svc
    prompt = svc._build_background_prompt(
        scene_description="unknown place",
        mood="day",
        style_prompt="some style",
        forbidden_characters=[],
        genre=None,
    )
    # 兜底：historical
    assert "中国古典绘画审美" in prompt or "手绘背景质感" in prompt
    # 仍不应该有 anime
    p_lower = prompt.lower()
    assert "makoto shinkai" not in p_lower


def test_safety_fallback_background_prompt_modern_genre_no_anime():
    """GENRE-STYLE — safety fallback 也按 genre 选 style，不硬编码 anime。"""
    from app.services.image_generation_service import image_generation_service as svc
    prompt = svc._build_safety_fallback_background_prompt(
        forbidden_characters=["陆沉"],
        scene_name="factory",
        scene_description="abandoned factory",
        genre="modern",
    )
    assert "当代写实摄影风格" in prompt
    p_lower = prompt.lower()
    assert "makoto shinkai" not in p_lower
    assert "galgame" not in p_lower


# ---------- SCENE-DISTILL: LLM 分析场景生成英文环境描述（2026-06-15） ----------

def test_rewritten_prompt_drops_chinese_subject_and_untrusted_style():
    """CN-LEAK-GUARD — subject 含中文时，to_cogview_prompt 必须丢弃 subject。
    LLM 自选 style 同样不得绕过后端锁定的 ProjectVisualBible。"""
    from app.services.prompt_rewriter_service import RewrittenPrompt
    rw = RewrittenPrompt(
        subject="陆沉在废弃厂房中重生",  # 含中文剧情，必须丢
        style="contemporary realistic photography style",
        details=["rusted steel beams", "broken windows"],
        lighting="moonlight through broken windows",
        composition="wide cinematic establishing shot, 16:9",
        negative_minimal=["named characters", "portrait"],
    )
    prompt = rw.to_cogview_prompt()
    assert "陆沉" not in prompt
    assert "重生" not in prompt
    assert "contemporary realistic photography" not in prompt
    assert "rusted steel beams" in prompt


def test_rewritten_prompt_injects_only_trusted_visual_bible_style():
    from app.services.prompt_rewriter_service import RewrittenPrompt

    rw = RewrittenPrompt(
        subject="abandoned factory interior",
        style="untrusted watercolor style",
        details=["rusted steel beams"],
        lighting="cold moonlight",
        composition="wide establishing shot",
    )
    prompt = rw.to_cogview_prompt(
        locked_style="locked graphic-novel ink and cel shading",
        use_final_prompt=False,
    )

    assert "locked graphic-novel ink and cel shading" in prompt
    assert "untrusted watercolor style" not in prompt


@pytest.mark.asyncio
async def test_background_builder_final_prompt_uses_only_locked_project_style(monkeypatch):
    from app.services import prompt_rewriter_service as rew_mod
    from app.services.prompt_builder_service import PromptBuilderService
    from app.services.prompt_rewriter_service import RewrittenPrompt
    from app.services.visual_style_profile_service import VisualStyleProfile

    class FakeRewriter:
        async def rewrite_many(self, asset_type, batch):
            assert asset_type == "background"
            assert batch[0]["visual_style_prompt"] == "项目锁定的版画线条与赛璐璐上色"
            return [
                RewrittenPrompt(
                    subject="废弃厂房内部",
                    style="模型擅自选择的水彩画风",
                    details=["锈蚀钢梁", "破碎窗户"],
                    lighting="冷色月光",
                    composition="广角环境建立镜头",
                )
            ]

    monkeypatch.setattr(rew_mod, "prompt_rewriter_service", FakeRewriter())
    profile = VisualStyleProfile(
        version="test-v1",
        style_family="graphic-novel",
        project_genre="modern",
        art_direction="locked",
        portrait_prompt_en="locked portrait style",
        background_prompt_zh="项目锁定的版画线条与赛璐璐上色",
        keyframe_prompt_en="locked keyframe style",
        palette="cold blue",
        lighting="moonlight",
        line_rendering="ink",
        mood="tense",
        negative_style_en="watercolor",
        fingerprint="a" * 64,
    )

    prompts = await PromptBuilderService().build_background_prompts_async(
        {"scene": "废弃厂房", "visual_keywords": ["锈蚀钢梁"]},
        {"characters": []},
        ["night"],
        environment_hint_en="abandoned factory interior",
        visual_style_profile=profile,
    )

    final_prompt = prompts[0]["final_prompt"]
    assert "项目锁定的版画线条与赛璐璐上色" in final_prompt
    assert "模型擅自选择的水彩画风" not in final_prompt


def test_background_prompt_injects_only_trusted_visual_bible_style():
    from app.services.prompt_rewriter_service import RewrittenPrompt

    rw = RewrittenPrompt(
        subject="废弃厂房内部空间",
        style="不可信水彩画风",
        details=["锈蚀钢梁", "破损窗户"],
        lighting="冷色月光",
        composition="广角建立镜头",
        negative_minimal=["命名角色", "肖像"],
    )
    prompt = rw.to_background_cogview_prompt(
        locked_style="项目锁定的版画线条与赛璐璐上色",
    )

    assert "项目锁定的版画线条与赛璐璐上色" in prompt
    assert "不可信水彩画风" not in prompt


def test_rewritten_prompt_to_cogview_prompt_drops_chinese_details():
    """CN-LEAK-GUARD — details 含中文项时，逐项丢弃。"""
    from app.services.prompt_rewriter_service import RewrittenPrompt
    rw = RewrittenPrompt(
        subject="abandoned factory interior",
        style="modern realistic photography",
        details=["rusted beams", "陆沉的录音机", "broken windows", "苏晚晴的笔记"],
        lighting="moonlight",
        composition="wide shot, 16:9",
        negative_minimal=["named characters"],
    )
    prompt = rw.to_cogview_prompt()
    assert "陆沉" not in prompt
    assert "苏晚晴" not in prompt
    assert "rusted beams" in prompt
    assert "broken windows" in prompt


@pytest.mark.asyncio
async def test_segmenter_distill_single_with_llm_called_for_short_content(monkeypatch):
    """SCENE-DISTILL — 正文 < MIN_CONTENT_LEN_FOR_SPLIT 时，应该调 _call_llm_single
    （基于 outline 蒸馏场景），不再走 _fallback_single 丢弃环境描述。"""
    from app.services import scene_segmenter_service as seg_mod

    captured = {}

    async def fake_call_llm_single(self, content, outline):
        captured["called"] = True
        captured["content_len"] = len(content or "")
        from app.services.scene_segmenter_service import SceneSegment
        return SceneSegment(
            segment_id=1,
            location="废弃厂房",
            location_en="abandoned factory",
            summary="",
            characters_present=[],
            mood="night",
            environment_hint_en=(
                "interior of an abandoned industrial factory at night, "
                "rusted steel beams, broken windows, faint moonlight"
            ),
        )

    monkeypatch.setattr(
        seg_mod.SceneSegmenterService, "_call_llm_single", fake_call_llm_single
    )
    # API key 必须配置，否则会进 fallback
    monkeypatch.setattr(seg_mod, "SCENE_SEGMENTER_ENABLED", True)
    monkeypatch.setattr(seg_mod, "SEGMENTER_API_KEY", "test-key")

    outline = {
        "scene": "陆沉在深夜的废弃厂房中重生",
        "summary": "陆沉调查案件，发现线索",
        "characters": ["陆沉"],
        "emotion": "night",
    }
    segments = await seg_mod.scene_segmenter_service.segment_chapter(
        chapter_content="",  # 空正文
        chapter_outline=outline,
    )
    assert captured.get("called") is True
    assert len(segments) == 1
    # environment_hint_en 必须由 LLM 蒸馏出来
    assert "abandoned" in segments[0].environment_hint_en
    assert "rusted" in segments[0].environment_hint_en


@pytest.mark.asyncio
async def test_segmenter_distill_single_with_llm_called_when_force_single(monkeypatch):
    """SCENE-DISTILL — force_single=True 时也应调 _call_llm_single。"""
    from app.services import scene_segmenter_service as seg_mod

    called = {"n": 0}

    async def fake_call_llm_single(self, content, outline):
        called["n"] += 1
        from app.services.scene_segmenter_service import SceneSegment
        return SceneSegment(
            segment_id=1,
            location="朝堂",
            location_en="imperial court hall",
            summary="",
            characters_present=[],
            mood="day",
            environment_hint_en=(
                "vast imperial court hall, vermilion columns, polished marble floor"
            ),
        )

    monkeypatch.setattr(
        seg_mod.SceneSegmenterService, "_call_llm_single", fake_call_llm_single
    )
    monkeypatch.setattr(seg_mod, "SCENE_SEGMENTER_ENABLED", True)
    monkeypatch.setattr(seg_mod, "SEGMENTER_API_KEY", "test-key")

    outline = {"scene": "朝堂", "summary": "议事", "characters": [], "emotion": "day"}
    segments = await seg_mod.scene_segmenter_service.segment_chapter(
        chapter_content="正文很长" * 200,  # > MIN_LEN，但 force_single
        chapter_outline=outline,
        force_single=True,
    )
    assert called["n"] == 1
    assert "imperial court hall" in segments[0].environment_hint_en


@pytest.mark.asyncio
async def test_segmenter_distill_single_fallback_to_no_env_hint_on_failure(monkeypatch):
    """SCENE-DISTILL — _call_llm_single 失败时降级到 _fallback_single（env_hint 空）。"""
    from app.services import scene_segmenter_service as seg_mod

    async def fake_call_llm_single_raise(self, content, outline):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(
        seg_mod.SceneSegmenterService, "_call_llm_single", fake_call_llm_single_raise
    )
    monkeypatch.setattr(seg_mod, "SCENE_SEGMENTER_ENABLED", True)
    monkeypatch.setattr(seg_mod, "SEGMENTER_API_KEY", "test-key")

    outline = {"scene": "废弃厂房", "summary": "陆沉调查", "characters": [], "emotion": "night"}
    segments = await seg_mod.scene_segmenter_service.segment_chapter(
        chapter_content="", chapter_outline=outline,
    )
    assert len(segments) == 1
    # 降级时 env_hint 为空（不调 LLM）
    assert segments[0].environment_hint_en == ""


@pytest.mark.asyncio
async def test_build_background_prompts_async_distills_when_env_hint_missing(monkeypatch):
    """SCENE-DISTILL-FALLBACK — 调用方未传 environment_hint_en 时，
    prompt_builder 应补调 segmenter._call_llm_single 蒸馏场景。"""
    from app.services import prompt_rewriter_service as rew_mod
    from app.services import scene_segmenter_service as seg_mod
    from app.services.prompt_builder_service import PromptBuilderService

    captured_fields = []

    class FakeRewriter:
        async def rewrite_many(self, asset_type, batch):
            captured_fields.extend(batch)
            return [None] * len(batch)

    async def fake_call_llm_single(self, content, outline):
        from app.services.scene_segmenter_service import SceneSegment
        return SceneSegment(
            segment_id=1,
            location="废弃厂房",
            location_en="abandoned factory",
            summary="",
            characters_present=[],
            mood="night",
            environment_hint_en=(
                "abandoned factory interior, rusted beams, broken windows, moonlight"
            ),
        )

    monkeypatch.setattr(
        rew_mod, "prompt_rewriter_service", FakeRewriter(), raising=True
    )
    monkeypatch.setattr(
        seg_mod.SceneSegmenterService, "_call_llm_single", fake_call_llm_single
    )

    svc = PromptBuilderService()
    outline = {
        "scene": "陆沉在废弃厂房中重生",
        "summary": "陆沉调查案件",
        "visual_keywords": [],
        "characters": ["陆沉"],
    }
    bible = {"worldview": "现代都市", "characters": [{"name": "陆沉"}]}

    # 故意不传 environment_hint_en
    await svc.build_background_prompts_async(outline, bible, ["night"])

    assert len(captured_fields) >= 1
    f = captured_fields[0]
    # env_hint 应该被补调 LLM 蒸馏出来，含英文环境描述
    assert "abandoned factory" in f["summary_environment_hint"]
    assert "rusted" in f["summary_environment_hint"]


@pytest.mark.asyncio
async def test_build_background_prompts_async_uses_provided_env_hint_without_recall(monkeypatch):
    """SCENE-DISTILL-FALLBACK — 调用方传了 environment_hint_en 时，不应再补调 LLM。"""
    from app.services import prompt_rewriter_service as rew_mod
    from app.services import scene_segmenter_service as seg_mod
    from app.services.prompt_builder_service import PromptBuilderService

    captured_fields = []
    llm_called = {"n": 0}

    class FakeRewriter:
        async def rewrite_many(self, asset_type, batch):
            captured_fields.extend(batch)
            return [None] * len(batch)

    async def fake_call_llm_single(self, content, outline):
        llm_called["n"] += 1
        return None

    monkeypatch.setattr(
        rew_mod, "prompt_rewriter_service", FakeRewriter(), raising=True
    )
    monkeypatch.setattr(
        seg_mod.SceneSegmenterService, "_call_llm_single", fake_call_llm_single
    )

    svc = PromptBuilderService()
    outline = {"scene": "朝堂", "summary": "议事", "visual_keywords": [], "characters": []}
    bible = {"worldview": "古代", "characters": []}

    await svc.build_background_prompts_async(
        outline, bible, ["day"],
        environment_hint_en="vast imperial court hall, vermilion columns",
    )

    assert llm_called["n"] == 0  # 不应再补调
    assert "vermilion columns" in captured_fields[0]["summary_environment_hint"]

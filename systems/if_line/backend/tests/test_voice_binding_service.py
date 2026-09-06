"""
VoiceBindingService 单元测试（in-memory SQLite，不依赖 dev DB）。

覆盖：
- 同 character_id 调 2 次 → 返回同一行（已绑定命中）
- 不同 character_id 同 vcn → (speed, pitch) 必须不同
- vcn 池用尽 → PARAM_GRID 差异化兜底（造 6 个男角色共用 x6_lingfeiyi_pro）
- IntegrityError 重读（mock db.add 抛 IntegrityError → 返回已存在的行）
- tts_service 缓存 key 含 speed/pitch/volume（回归）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.database import Base
from app.models import CharacterVoiceBinding  # noqa: E402  (after sys.path setup)
from app.services.voice_binding_service import VoiceBindingService


# ----------------------- in-memory DB fixture -----------------------

@pytest.fixture
def db_session():
    """每测一个独立 in-memory SQLite engine，建全表（含 CharacterVoiceBinding）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture
def fixed_matcher():
    """mock tts_service.match_voice_profile 总返回同一 vcn + 空的同性别 vcn 池，
    模拟"独占失败，必须走参数差异化"的退化场景（验证 PARAM_GRID 兜底）。

    旧测试（test_distinct_characters_get_distinct_voice_slots /
    test_vcn_pool_exhausted_uses_param_grid）需要这个退化场景，
    因为新算法默认会先独占同性别其他 vcn。
    """
    with patch(
        "app.services.tts_service.tts_service.match_voice_profile",
        return_value=("x6_lingfeiyi_pro", "calm"),
    ), patch(
        "app.services.tts_service.tts_service._lookup_profile_extras",
        return_value={"tte": "calm", "speed": 50, "pitch": 50, "volume": 50},
    ), patch.object(
        VoiceBindingService, "_same_gender_vcn_list",
        return_value=[],  # 模拟"没有其他同性别 vcn 可独占"
    ):
        yield


# ----------------------- 1. 同角色复用 -----------------------

def test_same_character_returns_same_binding(db_session, fixed_matcher):
    """同 character_id 调 2 次 → 返回同一行 id。"""
    svc = VoiceBindingService(db_session)
    b1 = svc.get_or_allocate(
        project_id=1, character_id="abc123def456",
        character_name="林夜", gender="male", age="young",
        character_voice="冷峻", emotion="calm",
    )
    b2 = svc.get_or_allocate(
        project_id=1, character_id="abc123def456",
        character_name="林夜", gender="male", age="young",
        character_voice="冷峻", emotion="calm",
    )
    assert b1.id == b2.id
    assert db_session.query(CharacterVoiceBinding).count() == 1


# ----------------------- 2. 不同角色独占 (vcn, speed, pitch) -----------------------

def test_distinct_characters_get_distinct_voice_slots(db_session, fixed_matcher):
    """2 个男角色都 match 到 x6_lingfeiyi_pro → 第二个的 (speed, pitch) 必须不同。"""
    svc = VoiceBindingService(db_session)
    b1 = svc.get_or_allocate(
        project_id=1, character_id="char_A",
        character_name="林夜", gender="male", age="young",
        character_voice="冷峻", emotion="calm",
    )
    b2 = svc.get_or_allocate(
        project_id=1, character_id="char_B",
        character_name="萧无", gender="male", age="middle",
        character_voice="沉稳", emotion="calm",
    )
    assert b1.vcn == b2.vcn == "x6_lingfeiyi_pro"
    assert (b1.speed, b1.pitch) != (b2.speed, b2.pitch), \
        f"two chars must differ on (speed,pitch), got {b1.speed},{b1.pitch} twice"


# ----------------------- 3. 池用尽走 PARAM_GRID -----------------------

def test_vcn_pool_exhausted_uses_param_grid(db_session, fixed_matcher):
    """造 6 个男角色共用 x6_lingfeiyi_pro → 6 行 binding 的 (speed, pitch) 全互异。"""
    svc = VoiceBindingService(db_session)
    slots: List[Tuple[int, int]] = []
    for i in range(6):
        b = svc.get_or_allocate(
            project_id=1, character_id=f"char_{i:02d}",
            character_name=f"角色{i}",
            gender="male", age="young",
            character_voice="冷峻", emotion="calm",
        )
        slots.append((b.speed, b.pitch))
    assert len(set(slots)) == 6, f"6 个角色必须有 6 个不同 (speed,pitch)，实际 {slots}"


# ----------------------- 4. IntegrityError 重读 -----------------------

def test_integrity_error_rereads_existing_row(db_session, fixed_matcher, monkeypatch):
    """并发模拟：第一条 add 抛 IntegrityError → 重读现有行返回。"""
    svc = VoiceBindingService(db_session)

    # 先正常插一条 char_A
    pre = svc.get_or_allocate(
        project_id=1, character_id="char_A",
        character_name="A", gender="male", age="young",
        character_voice="x", emotion="calm",
    )
    pre_id = pre.id

    # 第二次：mock commit 抛 IntegrityError（模拟另一并发事务先插了同一 character_id）
    real_commit = db_session.commit
    call_count = {"n": 0}

    def boom_commit():
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 真实写入数据但报错（模拟另一事务先提交），让重读能拿到行
            real_commit()
            raise IntegrityError("simulated", {}, Exception())
        return real_commit()

    monkeypatch.setattr(db_session, "commit", boom_commit)
    b = svc.get_or_allocate(
        project_id=1, character_id="char_A",  # 同 character_id
        character_name="A", gender="male", age="young",
        character_voice="x", emotion="calm",
    )
    assert b.id == pre_id
    assert db_session.query(CharacterVoiceBinding).count() == 1


# ----------------------- 5. 缓存 key 含 speed/pitch/volume（回归） -----------------------

def test_cache_key_includes_params():
    """synthesize 入参不同 speed/pitch → _get_cache_key 返回不同 key。"""
    from app.services.tts_service import TTSService
    svc = TTSService()
    k1 = svc._get_cache_key(
        "你好", "林夜", "冷峻", "x6_lingfeiyi_pro", "calm",
        speed=50, pitch=50, volume=50,
    )
    k2 = svc._get_cache_key(
        "你好", "林夜", "冷峻", "x6_lingfeiyi_pro", "calm",
        speed=55, pitch=45, volume=50,
    )
    k3 = svc._get_cache_key(
        "你好", "林夜", "冷峻", "x6_lingfeiyi_pro", "calm",
        speed=50, pitch=50, volume=50,
    )
    assert k1 != k2, "different speed/pitch must produce different cache keys"
    assert k1 == k3, "same params must produce same cache key"


# ----------------------- 6. 旁白不绑定（隐式：通过 ChapterVoiceService 路径已覆盖；
#                       这里单测 get_or_allocate 不对 None character_id 调用）-----------------------


# ----------------------- 7. PARAM_GRID 顺序确定 -----------------------

def test_param_grid_is_deterministic_and_unique():
    """PARAM_GRID 应为 25 个互不重复的 (speed, pitch) 槽。"""
    grid = VoiceBindingService.PARAM_GRID
    assert len(grid) == 25
    assert len(set(grid)) == 25
    # 首槽是 (50, 50) — 与 profile 默认一致
    assert grid[0] == (50, 50)


# ----------------------- 8. 独占 vcn 优先（多 vcn 池场景） -----------------------

def test_second_male_char_switches_to_unused_vcn():
    """2 个男角色都 match 到 x6_lingfeiyi_pro，且男声池还有飞博/伯松可用
    → 第二个角色自动切换到同性别其他未占用 vcn（独占优先，而非参数差异化）。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # mock matcher 总返回飞逸（模拟两个男角色都打中冷静青年 profile）
        with patch(
            "app.services.tts_service.tts_service.match_voice_profile",
            return_value=("x6_lingfeiyi_pro", "calm"),
        ), patch(
            "app.services.tts_service.tts_service._lookup_profile_extras",
            return_value={"tte": "calm", "speed": 50, "pitch": 50, "volume": 50},
        ):
            svc = VoiceBindingService(db)
            b1 = svc.get_or_allocate(
                project_id=1, character_id="m_char_A",
                character_name="A", gender="male", age="young",
                character_voice="冷峻", emotion="calm",
            )
            b2 = svc.get_or_allocate(
                project_id=1, character_id="m_char_B",
                character_name="B", gender="male", age="young",
                character_voice="冷峻", emotion="calm",
            )
        # 独占优先：两个男角色用不同 vcn，而不是共用飞逸 + 参数差异化
        assert b1.vcn != b2.vcn, \
            f"two male chars should pick different vcn (got {b1.vcn} twice)"
        assert {b1.vcn, b2.vcn}.issubset({
            "x6_lingfeiyi_pro", "x6_lingfeibo_pro", "x6_lingbosong_pro",
        }), f"unexpected vcn pair: {b1.vcn}, {b2.vcn}"
        # 切换 vcn 后参数是默认 (50, 50)
        assert (b1.speed, b1.pitch) == (50, 50)
        assert (b2.speed, b2.pitch) == (50, 50)
    finally:
        db.close()
        engine.dispose()


# ----------------------- 9. 池用尽才走参数差异化 -----------------------

def test_vcn_pool_exhausted_falls_back_to_param_diff():
    """3 个男 vcn 全占用后，第 4 个男角色走参数差异化（共用某 vcn + 改 speed/pitch）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        with patch(
            "app.services.tts_service.tts_service.match_voice_profile",
            return_value=("x6_lingfeiyi_pro", "calm"),
        ), patch(
            "app.services.tts_service.tts_service._lookup_profile_extras",
            return_value={"tte": "calm", "speed": 50, "pitch": 50, "volume": 50},
        ):
            svc = VoiceBindingService(db)
            bindings = []
            for i in range(4):
                b = svc.get_or_allocate(
                    project_id=1, character_id=f"m_char_{i}",
                    character_name=f"M{i}",
                    gender="male", age="young",
                    character_voice="冷峻", emotion="calm",
                )
                bindings.append(b)
        vcns_used = [b.vcn for b in bindings]
        slots = [(b.vcn, b.speed, b.pitch) for b in bindings]
        # 前 3 个角色独占 3 个男 vcn
        assert len(set(vcns_used[:3])) == 3, \
            f"first 3 chars should独占 3 vcn, got {vcns_used[:3]}"
        # 4 个角色 (vcn, speed, pitch) 全互异
        assert len(set(slots)) == 4, \
            f"4 chars must have 4 distinct (vcn, speed, pitch), got {slots}"
    finally:
        db.close()
        engine.dispose()


# ----------------------- 6. MiniMax 独占 -----------------------

@pytest.fixture
def mm_filled_matcher():
    """mock 让 matcher 总返回 x6_lingfeiyi_pro；同性别 vcn 池只给 1 个备选，
   逼迫第 3+ 个角色走参数差异化。但 minimax voice_id 池给真实派生结果，
    让 minimax 独占链路能验证。
    """
    with patch(
        "app.services.tts_service.tts_service.match_voice_profile",
        return_value=("x6_lingfeiyi_pro", "calm"),
    ), patch(
        "app.services.tts_service.tts_service._lookup_profile_extras",
        return_value={"tte": "calm", "speed": 50, "pitch": 50, "volume": 50},
    ):
        yield


def _new_mm_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session(), engine


def test_minimax_voice_id_populated_on_new_binding(db_session, mm_filled_matcher):
    """新分配的 binding 必须填 minimax_voice_id/speed/pitch/volume。"""
    svc = VoiceBindingService(db_session)
    b = svc.get_or_allocate(
        project_id=1, character_id="mm_char_A",
        character_name="甲", gender="male", age="young",
        character_voice="冷静", emotion="calm",
    )
    assert b.minimax_voice_id, "minimax_voice_id must be set"
    assert b.minimax_speed is not None
    assert b.minimax_pitch is not None
    assert b.minimax_volume == 50  # 默认 50


def test_minimax_primary_uses_gender_pool_when_legacy_speaker_has_no_profile(db_session):
    """主引擎占位 speaker 无映射时，首个女性角色也必须拿到女性 MiniMax 音色。"""
    with patch(
        "app.services.tts_service.tts_service.match_voice_profile",
        return_value=("male-qn-qingse", "happy"),
    ), patch(
        "app.services.tts_service.tts_service._lookup_profile_extras",
        return_value={"tte": "happy", "speed": 50, "pitch": 50, "volume": 50},
    ):
        binding = VoiceBindingService(db_session).get_or_allocate(
            project_id=1,
            character_id="mm_female_primary",
            character_name="苏婉",
            gender="female",
            age="young",
            character_voice="温柔少女女声",
            emotion="happy",
        )

    assert binding.minimax_voice_id == "female-shaonv"
    assert binding.minimax_voice_id != "male-qn-qingse"


def test_distinct_characters_get_distinct_minimax_voice_ids(db_session, mm_filled_matcher):
    """3 个男角色（matcher 都返回 x6_lingfeiyi_pro）→ 各自 minimax_voice_id 不同。"""
    svc = VoiceBindingService(db_session)
    vids = []
    for i in range(3):
        b = svc.get_or_allocate(
            project_id=1, character_id=f"mm_dist_{i}",
            character_name=f"M{i}",
            gender="male", age="young",
            character_voice="冷静", emotion="calm",
        )
        vids.append(b.minimax_voice_id)
    assert len(set(vids)) == 3, \
        f"3 chars must have 3 distinct minimax_voice_id, got {vids}"


def test_minimax_param_diff_when_voice_id_pool_exhausted(mm_filled_matcher):
    """同性别 minimax voice_id 池用尽后，叠 PARAM_GRID 参数差异化。

    构造：mock 主引擎也固定 vcn（_allocate_primary_slot 不切换）+ 同性别 vcn
    池为空（不切换 vcn）+ minimax voice_id 池为空（不切换 voice_id），
    造 3 个男角色 → 必须靠 minimax (speed, pitch) 区分。
    """
    db, engine = _new_mm_db()
    try:
        with patch.object(
            VoiceBindingService, "_same_gender_vcn_list",
            return_value=[],  # 主引擎也不切换 vcn
        ), patch.object(
            VoiceBindingService, "_same_gender_minimax_voice_ids",
            return_value=[],  # minimax 池里没有其他 voice_id 可独占
        ):
            svc = VoiceBindingService(db)
            bindings = []
            for i in range(3):
                b = svc.get_or_allocate(
                    project_id=1, character_id=f"mm_pool_{i}",
                    character_name=f"P{i}",
                    gender="male", age="young",
                    character_voice="冷静", emotion="calm",
                )
                bindings.append(b)
        vids = [b.minimax_voice_id for b in bindings]
        slots = [(b.minimax_speed, b.minimax_pitch) for b in bindings]
        # 三个角色共用同一 voice_id
        assert len(set(vids)) == 1, f"all 3 should share voice_id, got {vids}"
        # (speed, pitch) 全互异
        assert len(set(slots)) == 3, \
            f"3 chars must differ on minimax (speed,pitch), got {slots}"
    finally:
        db.close()
        engine.dispose()


def test_lazy_fill_minimax_for_legacy_binding(db_session, mm_filled_matcher):
    """旧 binding 没有 minimax 字段 → 二次访问时 lazy 补全。"""
    from app.models import CharacterVoiceBinding
    # 手动插一条老 binding（minimax_* 全 NULL）
    legacy = CharacterVoiceBinding(
        project_id=1, character_id="legacy_char",
        character_name="老角色",
        vcn="x6_lingfeiyi_pro",
        speed=50, pitch=50, volume=50,
        emotion="calm",
        # minimax_* 故意不填
    )
    db_session.add(legacy)
    db_session.commit()

    svc = VoiceBindingService(db_session)
    b = svc.get_or_allocate(
        project_id=1, character_id="legacy_char",
        character_name="老角色", gender="male",
    )
    assert b.id == legacy.id
    assert b.minimax_voice_id, "lazy-fill must populate minimax_voice_id"
    assert b.minimax_speed is not None
    assert b.minimax_pitch is not None


def test_minimax_slot_unique_across_project(db_session, mm_filled_matcher):
    """跨角色：同项目内 (minimax_voice_id, minimax_speed, minimax_pitch) 三元组互异。"""
    svc = VoiceBindingService(db_session)
    slots = []
    for i in range(5):
        b = svc.get_or_allocate(
            project_id=1, character_id=f"uniq_{i}",
            character_name=f"U{i}",
            gender="male", age="young",
            character_voice="冷静", emotion="calm",
        )
        slots.append((b.minimax_voice_id, b.minimax_speed, b.minimax_pitch))
    assert len(set(slots)) == 5, \
        f"5 chars must have 5 distinct minimax slots, got {slots}"

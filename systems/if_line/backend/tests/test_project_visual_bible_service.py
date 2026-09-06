"""project-visual-bible-v2 §F1 — ProjectVisualBible service invariants.

These tests pin the contract documented in plan §A / §B:

* fingerprint is stable across calls and processes;
* once locked_at is set, subsequent ``build_for_project`` calls preserve
  the lock (chapter LLMs cannot rewrite the project bible);
* ``assert_bible_locked`` raises before any asset generation if the lock
  is missing;
* visual_medium is decided by explicit keywords (水墨 / 写实) only, not
  by narrative genre — modern/sci-fi can still use anime_cel as their
  medium.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.project_visual_bible_service import (
    ProjectVisualBible,
    ProjectVisualBibleService,
    project_visual_bible_service,
    SCHEMA_KEY,
    VERSION,
)


def _modern_campus_sb():
    return {
        "raw_json": {},
        "worldview": "2020 年代某普通一本大学，计算机科学与技术专业。",
        "style_rules": "轻松幽默为主基调，叙事节奏明快。",
        "characters": [
            {"name": "李明", "gender": "男", "appearance": "黑框眼镜，格子衫"},
        ],
    }


def _ink_guofeng_sb():
    return {
        "raw_json": {},
        "worldview": "古代唐朝长安城",
        "style_rules": "水墨工笔风格，工笔重彩",
        "characters": [{"name": "李白", "gender": "男"}],
    }


def _scifi_sb():
    return {
        "raw_json": {},
        "worldview": "23 世纪人类殖民火星，赛博朋克",
        "style_rules": "科幻惊悚，霓虹灯",
        "characters": [{"name": "Rei", "gender": "女"}],
    }


# ----------------------------------------------------------------------
# F1-A: fingerprint stability
# ----------------------------------------------------------------------

def test_fingerprint_stable_across_calls():
    sb = _modern_campus_sb()
    b1 = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    b2 = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    assert b1.fingerprint == b2.fingerprint
    assert len(b1.fingerprint) == 16


def test_fingerprint_shifts_when_locked_fields_change():
    """Hard-constraint: any of the locked ★ fields changing must move the
    fingerprint, otherwise cache keys would silently collide."""
    sb_anime = _modern_campus_sb()
    b_anime = project_visual_bible_service.build_for_project(sb_anime, project_title="T", project_style="")

    sb_ink = _ink_guofeng_sb()
    b_ink = project_visual_bible_service.build_for_project(sb_ink, project_title="T", project_style="")
    assert b_anime.fingerprint != b_ink.fingerprint
    assert b_anime.style_family != b_ink.style_family


# ----------------------------------------------------------------------
# F1-B: locked_at — chapter LLM cannot rewrite the project bible
# ----------------------------------------------------------------------

def test_build_for_project_persists_locked_at():
    sb = _modern_campus_sb()
    project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    assert project_visual_bible_service.is_locked(sb)
    locked = project_visual_bible_service.load_locked(sb)
    assert isinstance(locked, ProjectVisualBible)
    assert locked.fingerprint


def test_second_build_does_not_reset_lock():
    """Once locked, a subsequent ``build_for_project`` call must preserve
    the locked_at timestamp — chapter-level code cannot rewrite the
    project-level art direction."""
    sb = _modern_campus_sb()
    project_visual_bible_service.build_for_project(sb, project_title="T1", project_style="")
    raw = sb["raw_json"][SCHEMA_KEY]
    locked_at_first = raw["locked_at"]

    # Second call — different title must not invalidate the lock.
    project_visual_bible_service.build_for_project(sb, project_title="T2", project_style="")
    raw_after = sb["raw_json"][SCHEMA_KEY]
    assert raw_after["locked_at"] == locked_at_first


# ----------------------------------------------------------------------
# F1-C: assert_bible_locked
# ----------------------------------------------------------------------

def test_assert_bible_locked_raises_before_lock():
    sb = _modern_campus_sb()
    # No build_for_project yet → not locked
    assert not project_visual_bible_service.is_locked(sb)
    try:
        project_visual_bible_service.assert_bible_locked(sb)
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "assert_bible_locked must raise before lock"


def test_assert_bible_locked_passes_after_lock():
    sb = _modern_campus_sb()
    project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    # Must not raise
    project_visual_bible_service.assert_bible_locked(sb)


# ----------------------------------------------------------------------
# F1-D: genre / medium decoupling (HARD constraint)
# ----------------------------------------------------------------------

def test_visual_medium_not_decided_by_genre():
    """modern campus must default to anime_cel unless an explicit
    水墨/工笔/写实 keyword appears. Genre decides setting/costume only."""
    sb = _modern_campus_sb()
    bible = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    cls = project_visual_bible_service.infer_classification(
        sb, project_title="T", project_style="",
    )
    assert cls.narrative_genre in ("modern_campus", "modern")
    assert bible.style_family == "anime_cel", (
        f"modern campus must default to anime_cel, got {bible.style_family}"
    )


def test_explicit_ink_keyword_switches_medium_not_genre():
    sb = _ink_guofeng_sb()
    bible = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    assert bible.style_family == "ink_color_guofeng"


def test_scifi_without_explicit_realism_stays_anime_cel():
    """sci-fi narrative must NOT silently switch to concept_art_realism
    unless the user explicitly asks for photorealism."""
    sb = _scifi_sb()
    bible = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    assert bible.style_family in ("anime_cel", "digital_painterly"), (
        f"sci-fi without explicit realism keyword must stay illustrated, "
        f"got {bible.style_family}"
    )


def test_personal_future_phrase_does_not_trigger_scifi_setting():
    """Ordinary life-planning language must not override a modern setting."""
    sb = {
        "raw_json": {
            "emotional_line": "林默在小店找到归属感，终于能够坦然面对未来。",
        },
        "worldview": "故事发生在中国南方小城的老街和一家经营多年的咖啡店。",
        "style_rules": "日常、温馨、治愈，注重生活细节。",
        "characters": [{"name": "林默", "appearance": "现代青年咖啡师"}],
    }

    classification = project_visual_bible_service.infer_classification(
        sb,
        project_title="《晚风里的小店》",
        project_style="治愈温馨",
    )

    assert classification.narrative_genre == "modern_campus"
    assert classification.setting_period == "contemporary East Asian everyday-life setting"
    assert "未来" not in classification.evidence


def test_explicit_future_setting_phrase_still_triggers_scifi():
    sb = {
        "raw_json": {},
        "worldview": "故事发生在近未来的海滨城市，人类与服务机器人共同生活。",
        "style_rules": "克制的社会科幻。",
        "characters": [],
    }

    classification = project_visual_bible_service.infer_classification(sb)

    assert classification.narrative_genre == "sci-fi"
    assert classification.setting_period == "near-future science-fiction setting"
    assert "近未来" in classification.evidence


def test_derived_historical_costume_does_not_reclassify_fantasy_world(monkeypatch):
    """Generated character cards are outputs, never project-genre evidence."""
    monkeypatch.setattr(
        project_visual_bible_service,
        "_call_llm_classifier",
        lambda **_: None,
    )
    sb = {
        "raw_json": {
            "source_work": "火影忍者",
            "characters": [
                {
                    "name": "波风水门",
                    "visual_description_cn": "身穿古代袍服",
                    "visual_profile": {
                        "world_style": "fantasy",
                        "outfit": {"style": "historical_robe"},
                    },
                }
            ],
        },
        "source_work": "火影忍者",
        "worldview": "忍者使用查克拉、忍术、血继限界与尾兽力量战斗。",
        "style_rules": "轻松幽默的少年冒险。",
        "characters": [
            {
                "name": "波风水门",
                "role": "导师",
                "appearance": "古代袍服",
                "visual_description_cn": "身穿古代袍服",
                "visual_profile": {
                    "world_style": "fantasy",
                    "outfit": {"style": "historical_robe"},
                },
            }
        ],
    }

    classification = project_visual_bible_service.infer_classification(
        sb,
        project_title="火影忍者：带土没有死",
        source_work="火影忍者",
    )

    assert classification.narrative_genre == "fantasy"
    assert classification.setting_period == "fantasy setting with supernatural power systems"
    assert "古代" not in classification.evidence
    assert set(classification.evidence) >= {"查克拉", "忍术"}


def test_genre_uses_score_instead_of_historical_first_match(monkeypatch):
    monkeypatch.setattr(
        project_visual_bible_service,
        "_call_llm_classifier",
        lambda **_: None,
    )
    sb = {
        "raw_json": {},
        "worldview": "古代架空世界，魔法、异能与神话生物共同存在。",
        "style_rules": "奇幻冒险",
        "characters": [],
    }

    classification = project_visual_bible_service.infer_classification(sb)

    assert classification.narrative_genre == "fantasy"
    assert "魔法" in classification.evidence


def test_short_latin_keyword_requires_word_boundary(monkeypatch):
    monkeypatch.setattr(
        project_visual_bible_service,
        "_call_llm_classifier",
        lambda **_: None,
    )
    sb = {
        "raw_json": {},
        "worldview": "A faithful fantasy realm governed by magic.",
        "style_rules": "Painterly adventure.",
        "characters": [],
    }

    classification = project_visual_bible_service.infer_classification(sb)

    assert classification.narrative_genre == "fantasy"
    assert "ai" not in classification.evidence


def test_v3_migration_reclassifies_genre_without_reclassifying_medium(monkeypatch):
    sb = {
        "raw_json": {},
        "worldview": "忍者使用查克拉、忍术与尾兽力量战斗。",
        "style_rules": "赛璐璐动画风格",
        "characters": [{"name": "宇智波带土"}],
    }
    monkeypatch.setattr(
        project_visual_bible_service,
        "_call_llm_classifier",
        lambda **_: None,
    )
    old_bible = project_visual_bible_service.build_for_project(sb)
    old_lock = sb["raw_json"][SCHEMA_KEY]
    old_lock["schema_version"] = "project-visual-bible-v3"
    old_lock["classification"]["narrative_genre"] = "historical_guofeng"
    old_lock["classification"]["setting_period"] = "Chinese classical historical setting"

    def fail_if_called(**_):
        raise AssertionError("v3 migration must not call the visual LLM again")

    monkeypatch.setattr(
        project_visual_bible_service,
        "_call_llm_classifier",
        fail_if_called,
    )
    migrated_bible = project_visual_bible_service.build_for_project(sb)
    migrated = project_visual_bible_service.load_locked_classification(sb)

    assert sb["raw_json"][SCHEMA_KEY]["schema_version"] == VERSION
    assert migrated is not None
    assert migrated.narrative_genre == "fantasy"
    assert migrated.visual_medium == "anime_cel"
    assert migrated_bible.style_family == old_bible.style_family
    assert "fantasy setting with supernatural power systems" in migrated_bible.art_direction

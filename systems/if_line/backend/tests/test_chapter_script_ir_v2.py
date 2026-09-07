from __future__ import annotations

import pytest

from app.application.hashing import content_hash
from app.services.chapter_script_ir import (
    SCRIPT_IR_SCHEMA_VERSION,
    SCRIPT_IR_SUPPORTED_SCHEMA_VERSIONS,
    ScriptIRValidationError,
    build_script_ir_from_segments,
    normalize_emotion,
    resource_slot_specs,
    validate_script_ir,
)


CHARACTERS = [
    {"character_id": "char-a", "name": "艾莉丝", "appearance": "银发少女"},
    {"character_id": "char-b", "name": "老太太"},
]


CONTENT = "艾莉丝走进酒馆。\n她看向老板。\n老太太叹了口气。"


def _build(segments: list[dict], characters=CHARACTERS, content: str = CONTENT):
    return build_script_ir_from_segments(
        chapter_revision_id="cr-1",
        chapter_content=content,
        chapter_content_hash=content_hash(content),
        bible_revision_id="bible-1",
        outline_revision_id="outline-1",
        characters=characters,
        llm_output={"segments": segments, "scenes": [{"scene_key": "s1", "title": "酒馆", "location": "酒馆"}]},
    )


def _line_segments():
    return [
        {"text": "艾莉丝走进酒馆。", "kind": "narration"},
        {"text": "她看向老板。", "kind": "dialogue", "speaker_character_id": "char-a"},
        {"text": "老太太叹了口气。", "kind": "dialogue", "speaker_character_id": "char-b"},
    ]


def test_build_script_ir_defaults_new_fields_when_absent():
    ir = _build(_line_segments())
    assert ir["schema_version"] == SCRIPT_IR_SCHEMA_VERSION == "script-ir-v3"
    assert "script-ir-v2" in SCRIPT_IR_SUPPORTED_SCHEMA_VERSIONS  # 存量数据仍可校验
    for paragraph in ir["paragraphs"]:
        assert paragraph["speaker_display_name"] is None
        assert paragraph["stage_events"] == []
    assert ir["annotation_gaps"] == []


def test_build_script_ir_captures_display_name_and_stage_events():
    ir = _build(
        [
            {"text": "艾莉丝走进酒馆。", "kind": "narration"},
            {
                "text": "她看向老板。",
                "kind": "dialogue",
                "speaker_character_id": "char-b",
                "speaker_display_name": "老太太",
                "stage_events": [{"type": "exit", "character_id": "char-a"}],
            },
            {
                "text": "老太太叹了口气。",
                "kind": "dialogue",
                "speaker_character_id": "char-b",
                "speaker_display_name": "奶奶",
                "stage_events": [],
            },
        ]
    )
    second, third = ir["paragraphs"][1], ir["paragraphs"][2]
    assert second["speaker_display_name"] == "老太太"
    assert second["stage_events"] == [{"type": "exit", "character_id": "char-a"}]
    assert third["speaker_display_name"] == "奶奶"


def test_build_script_ir_drops_invalid_stage_events():
    ir = _build(
        [
            {
                "text": "艾莉丝走进酒馆。",
                "kind": "narration",
                "stage_events": [
                    {"type": "exit", "character_id": "char-unknown"},
                    {"type": "dance", "character_id": "char-a"},
                    {"type": "enter", "character_id": "char-a"},
                    "garbage",
                ],
            },
            {"text": "她看向老板。", "kind": "narration"},
            {"text": "老太太叹了口气。", "kind": "narration"},
        ]
    )
    assert ir["paragraphs"][0]["stage_events"] == [
        {"type": "enter", "character_id": "char-a"}
    ]


def test_validate_script_ir_accepts_supported_schema_versions():
    ir = _build(_line_segments())
    content = CONTENT
    for version in SCRIPT_IR_SUPPORTED_SCHEMA_VERSIONS:
        ir["schema_version"] = version
        validate_script_ir(ir, expected_content=content)


def test_validate_script_ir_rejects_unknown_schema_version():
    ir = _build(_line_segments())
    ir["schema_version"] = "script-ir-v0"
    with pytest.raises(ScriptIRValidationError):
        validate_script_ir(ir)


def test_normalize_emotion_collapses_whitespace_and_case():
    assert normalize_emotion("  Happy  ") == "happy"
    assert normalize_emotion("轻微 愤怒") == "轻微 愤怒"
    assert normalize_emotion(None) == ""


MULTI_SCENE_CONTENT = (
    "艾莉丝走进酒馆。\n"
    "「今天真冷。」她搓着手说。\n"
    "老太太叹了口气。\n"
    "艾莉丝走出酒馆来到街头。\n"
    "「这里就是传说中的广场。」\n"
    "她愤怒地握紧拳头。\n"
    "又慢慢平静下来。\n"
)


def _multi_scene_ir():
    return build_script_ir_from_segments(
        chapter_revision_id="cr-2",
        chapter_content=MULTI_SCENE_CONTENT,
        chapter_content_hash=content_hash(MULTI_SCENE_CONTENT),
        bible_revision_id="bible-1",
        outline_revision_id="outline-1",
        characters=CHARACTERS,
        llm_output={
            "segments": [
                {"text": "艾莉丝走进酒馆。", "scene_key": "s1", "kind": "narration"},
                {"text": "「今天真冷。」她搓着手说。", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "Happy"},
                {"text": "老太太叹了口气。", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-b", "emotion": "sad"},
                {"text": "艾莉丝走出酒馆来到街头。", "scene_key": "s2", "kind": "narration"},
                {"text": "「这里就是传说中的广场。」", "scene_key": "s2", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
                {"text": "她愤怒地握紧拳头。", "scene_key": "s2", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "angry"},
                {"text": "又慢慢平静下来。", "scene_key": "s2", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "angry"},
            ],
            "scenes": [
                {"scene_key": "s1", "title": "酒馆", "location": "酒馆"},
                {"scene_key": "s2", "title": "街头", "location": "街头"},
            ],
        },
    )


def test_portrait_slots_dedupe_by_character_and_emotion_chapter_wide():
    specs = resource_slot_specs(_multi_scene_ir())
    portraits = [spec for spec in specs if spec["role"] == "portrait"]
    by_key = {spec["slot_key"]: spec for spec in portraits}

    # char-a: happy(跨场景跨段落归一) + angry；char-b: sad → 共 3 个立绘槽
    assert set(by_key) == {
        "portrait:char-a:happy",
        "portrait:char-a:angry",
        "portrait:char-b:sad",
    }
    # happy 槽锚定首现段落（s1 的第 2 段），而非 s2 中的重复情绪段
    assert by_key["portrait:char-a:happy"]["paragraph_id"] == "paragraph-0002"
    assert by_key["portrait:char-a:happy"]["scene_id"] == "scene-001"
    assert by_key["portrait:char-a:angry"]["paragraph_id"] == "paragraph-0006"
    assert all(spec["required"] for spec in portraits)


def test_keyframe_slots_remain_per_paragraph():
    ir = _multi_scene_ir()
    ir["paragraphs"][0]["keyframe_required"] = True
    ir["paragraphs"][0]["keyframe_prompt"] = "雨夜灯光"
    specs = resource_slot_specs(ir)
    keyframes = [spec for spec in specs if spec["role"] == "keyframe"]
    assert len(keyframes) == 1
    assert keyframes[0]["paragraph_id"] == "paragraph-0001"


def test_background_slots_remain_one_per_scene():
    specs = resource_slot_specs(_multi_scene_ir())
    backgrounds = [spec for spec in specs if spec["role"] == "background"]
    assert [spec["scene_id"] for spec in backgrounds] == ["scene-001", "scene-002"]
    assert all(spec["prompt"].endswith("无人，无文字") for spec in backgrounds)


def test_appearance_flows_into_portrait_prompt():
    specs = resource_slot_specs(_multi_scene_ir())
    happy = next(spec for spec in specs if spec["slot_key"] == "portrait:char-a:happy")
    assert "银发少女" in happy["prompt"]
    sad = next(spec for spec in specs if spec["slot_key"] == "portrait:char-b:sad")
    assert "（" not in sad["prompt"]  # 无 appearance 的角色不产生空括号


def test_long_emotions_with_shared_prefix_do_not_collide_slot_keys():
    long_a = "轻微" + "的" * 60
    long_b = "轻微" + "的" * 60 + "但是不同"
    ir = _build(
        [
            {"text": "艾莉丝走进酒馆。", "kind": "dialogue",
             "speaker_character_id": "char-a", "emotion": long_a},
            {"text": "她看向老板。", "kind": "dialogue",
             "speaker_character_id": "char-a", "emotion": long_b},
            {"text": "老太太叹了口气。", "kind": "narration"},
        ]
    )
    portraits = [spec for spec in resource_slot_specs(ir) if spec["role"] == "portrait"]
    slot_keys = [spec["slot_key"] for spec in portraits]
    assert len(portraits) == 2
    assert len(set(slot_keys)) == 2


def _presence_ir(stage_events_by_paragraph=None):
    """char-a(主角)第 1 段 enter 登场后全程旁白不说话,char-b 第 2 段 enter 登场后有台词。"""
    stage_events_by_paragraph = stage_events_by_paragraph or {
        1: [{"type": "enter", "character_id": "char-a"}],
        2: [{"type": "enter", "character_id": "char-b"}],
    }
    segments = [
        {"text": "艾莉丝走进酒馆。", "kind": "narration",
         "stage_events": stage_events_by_paragraph.get(1, [])},
        {"text": "老太太推门走了进来。", "kind": "narration",
         "stage_events": stage_events_by_paragraph.get(2, [])},
        {"text": "「坐吧。」老太太说。", "kind": "dialogue", "speaker_character_id": "char-b"},
    ]
    joined = "".join(seg["text"] for seg in segments)
    return _build(segments, content=joined)


def test_present_non_speaker_gets_neutral_scene_portrait_slot():
    ir = _presence_ir()
    portraits = [spec for spec in resource_slot_specs(ir) if spec["role"] == "portrait"]
    by_key = {spec["slot_key"]: spec for spec in portraits}
    # char-b 有台词(speaker 槽)；char-a enter 后全程旁白 → 补 neutral-scene 槽
    assert "portrait:char-b:neutral" in by_key
    assert "portrait:char-a:neutral-scene" in by_key
    scene_slot = by_key["portrait:char-a:neutral-scene"]
    assert scene_slot["emotion"] == "neutral"
    assert scene_slot["paragraph_id"] == "paragraph-0001"  # 锚定首次在场(enter)段落
    assert scene_slot["required"] is True
    assert scene_slot["target_name"] == "艾莉丝"


def test_neutral_scene_slot_prompt_carries_appearance():
    ir = _presence_ir()
    portraits = [spec for spec in resource_slot_specs(ir) if spec["role"] == "portrait"]
    scene_slot = next(spec for spec in portraits if spec["slot_key"].endswith("neutral-scene"))
    assert "银发少女" in scene_slot["prompt"]


def test_enter_without_dialogue_still_gets_slot_anchored_at_enter():
    ir = _presence_ir()
    portraits = [spec for spec in resource_slot_specs(ir) if spec["role"] == "portrait"]
    by_key = {spec["slot_key"]: spec for spec in portraits}
    # char-b 的 speaker 槽已存在,不再额外补 neutral-scene 槽
    assert "portrait:char-b:neutral-scene" not in by_key


def test_character_exited_before_scene_end_gets_no_extra_slot():
    # char-b 第 2 段 enter、第 3 段 exit 且不说话,场景末不在台上 → 不补
    segments = [
        {"text": "艾莉丝走进酒馆。", "kind": "narration",
         "stage_events": [{"type": "enter", "character_id": "char-a"}]},
        {"text": "老太太推门走了进来。", "kind": "narration",
         "stage_events": [{"type": "enter", "character_id": "char-b"}]},
        {"text": "老太太又转身离开。", "kind": "narration",
         "stage_events": [{"type": "exit", "character_id": "char-b"}]},
    ]
    ir = _build(segments, content="".join(seg["text"] for seg in segments))
    portraits = [spec for spec in resource_slot_specs(ir) if spec["role"] == "portrait"]
    slot_keys = {spec["slot_key"] for spec in portraits}
    assert "portrait:char-b:neutral-scene" not in slot_keys
    assert "portrait:char-a:neutral-scene" in slot_keys


def test_segmentation_prompt_enforces_dialogue_purity_and_merging():
    from app.integrations.llm.chapter_script_adapter import (
        CHAPTER_SCRIPT_PROMPT_VERSION,
        CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT,
    )

    assert CHAPTER_SCRIPT_PROMPT_VERSION == "chapter-script-llm-segments-visible-source-v3"
    # 对白纯净铁律：归属/动作禁止混入 dialogue segment
    assert "【对白纯净——铁律】" in CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT
    assert "绝对禁止出现在 dialogue segment" in CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT
    assert "会被当作台词念出来" in CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT
    assert "以开引号开头、以闭引号结尾" in CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT
    # 台词成段：同说话人紧邻引语必须合并
    assert "【台词成段】" in CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT
    assert "必须合并为一个 dialogue segment" in CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT

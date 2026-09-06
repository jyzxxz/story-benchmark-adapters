from __future__ import annotations

import pytest

from app.application.hashing import content_hash
from app.services.chapter_script_ir import build_script_ir_from_segments
from app.services.vn_graph_compiler import (
    STAGE_DIM_ALPHA,
    STAGE_PORTRAIT_TOP_Y,
    VNGRAPH_COMPILER_VERSION,
    VNGRAPH_TACHI_POLICY_VERSION,
    VNGraphCompileInput,
    stage_slot_anchor,
    vn_graph_compiler,
)


def _anchor_x(count: int, slot: str) -> float:
    return stage_slot_anchor(count, slot)[0]

ACTION = 2
SUBTYPE_TACHI = 1
SUBTYPE_BACKGROUND = 3
SUBTYPE_ART = 6
SUBTYPE_TACHI_MOVE = 11
SUBTYPE_TACHI_EXIT = 13
SUBTYPE_TACHI_CHANGE_IMAGE = 26
SUBTYPE_CLEAR_ALL_TACHIS = 33
SUBTYPE_CLEAR_ILLUSTRATION = 37
SUBTYPE_TACHI_HIGHLIGHT = 38

CHARACTERS = [
    {"character_id": "char-a", "name": "艾莉丝"},
    {"character_id": "char-b", "name": "诺克"},
    {"character_id": "char-c", "name": "米娅"},
]


def _ir(annotations: dict, content: str, schema_override: str | None = None):
    # v3：把逐段 annotations 转成 segments（一段=一非空行，顺序即原文顺序）
    lines = [line for line in content.splitlines() if line.strip()]
    items = annotations.get("paragraphs") or []
    assert len(items) == len(lines), "测试 fixture 的段数与正文非空行数不一致"
    segments = []
    for item, line in zip(items, lines):
        segment = {k: v for k, v in item.items() if k != "paragraph_id"}
        segment["text"] = line
        segments.append(segment)
    ir = build_script_ir_from_segments(
        chapter_revision_id="cr-test",
        chapter_content=content,
        chapter_content_hash=content_hash(content),
        bible_revision_id="bible-1",
        outline_revision_id="outline-1",
        characters=CHARACTERS,
        llm_output={"segments": segments, "scenes": annotations.get("scenes") or []},
    )
    if schema_override:
        ir["schema_version"] = schema_override
        ir = dict(ir)
    return ir


def _binding(
    slot_id: str,
    role: str,
    paragraph_id: str,
    scene_id: str,
    character_id: str | None = None,
    emotion: str | None = None,
    url: str | None = None,
) -> dict:
    media_url = url or f"https://cdn.example/{slot_id}.png"
    return {
        "resource_slot_id": slot_id,
        "binding_id": f"binding-{slot_id}",
        "role": role,
        "scene_id": scene_id,
        "paragraph_id": paragraph_id,
        "character_id": character_id,
        "order_index": 0,
        "required": True,
        "slot_status": "bound",
        "asset_version_id": f"ver-{slot_id}",
        "asset_version_hash": f"hash-{slot_id}",
        "media_url": media_url,
        "emotion": emotion,
        "target_name": character_id,
    }


def _compile(ir, bindings):
    source = VNGraphCompileInput(
        project_id=1,
        display_index=1,
        chapter_revision_id="cr-test",
        chapter_content_hash=ir["source"]["content_hash"],
        chapter_content=_CONTENT_CACHE.get(id(ir), "") or _rebuild_content(ir),
        script_revision_id="script-1",
        script_hash=content_hash(ir),
        script_ir=ir,
        asset_bindings=bindings,
    )
    return vn_graph_compiler.compile(source)


_CONTENT_CACHE: dict[int, str] = {}


def _rebuild_content(ir) -> str:
    return "".join(span["text"] for span in ir["spans"])


def _actions_of(graph, paragraph_index: int) -> list[dict]:
    nodes_by_index = {node["Index"]: node for node in graph["Nodes"]}
    paragraph_node = nodes_by_index[paragraph_index]
    action_indices = paragraph_node.get("Outputs", {}).get("Actions", [])
    return [nodes_by_index[index] for index in action_indices]


def _flat_actions(graph) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    for node in graph["Nodes"]:
        if node.get("NodeType") == 1 and node.get("SubType") == 2:
            actions = node.get("Outputs", {}).get("Actions", [])
            if actions:
                result[node["Index"]] = [
                    next(n for n in graph["Nodes"] if n["Index"] == idx) for idx in actions
                ]
    return result


def _data_value(node, key):
    return node["Data"][key]


SINGLE_SCENE_CONTENT = (
    "艾莉丝走进酒馆。\n"
    "「你好。」艾莉丝说。\n"
    "「随便看看。」艾莉丝继续说。\n"
    "「再来一句。」艾莉丝还说。\n"
)


def _single_scene_ir(extra: dict | None = None):
    annotations = {
        "paragraphs": [
            {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
            {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
             "speaker_character_id": "char-a", "emotion": "happy"},
            {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "dialogue",
             "speaker_character_id": "char-a", "emotion": "happy"},
            {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
             "speaker_character_id": "char-a", "emotion": "happy"},
        ],
        "scenes": [{"scene_key": "s1", "title": "酒馆", "location": "酒馆"}],
    }
    for paragraph in annotations["paragraphs"]:
        paragraph.update((extra or {}).get(paragraph["paragraph_id"], {}))
    return _ir(annotations, SINGLE_SCENE_CONTENT)


def test_first_appearance_only_no_flicker_on_repeated_emotion():
    ir = _single_scene_ir()
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001",
                 "char-a", "happy"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    tachi_nodes = [
        node
        for actions in flat.values()
        for node in actions
        if node["SubType"] == SUBTYPE_TACHI
    ]
    change_nodes = [
        node
        for actions in flat.values()
        for node in actions
        if node["SubType"] == SUBTYPE_TACHI_CHANGE_IMAGE
    ]
    assert len(tachi_nodes) == 1
    assert tachi_nodes[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert tachi_nodes[0]["Data"]["TachiIamge"]["StringValue"].endswith("p-a-happy.png")
    assert change_nodes == []


def test_emotion_switch_emits_change_image_and_return_reuses_slot():
    ir = _single_scene_ir(
        {
            "paragraph-0003": {"emotion": "sad"},
            "paragraph-0004": {"emotion": "happy"},
        }
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
        _binding("p-a-sad", "portrait", "paragraph-0003", "scene-001", "char-a", "sad"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    para3 = flat[4]
    para4 = flat[5]
    change3 = [node for node in para3 if node["SubType"] == SUBTYPE_TACHI_CHANGE_IMAGE]
    change4 = [node for node in para4 if node["SubType"] == SUBTYPE_TACHI_CHANGE_IMAGE]
    assert len(change3) == 1
    assert change3[0]["Data"]["TachiIamge"]["StringValue"].endswith("p-a-sad.png")
    # 情绪回切：第四段换回 happy 槽的图，不产生新 TACHI
    assert len(change4) == 1
    assert change4[0]["Data"]["TachiIamge"]["StringValue"].endswith("p-a-happy.png")
    assert not [node for node in para4 if node["SubType"] == SUBTYPE_TACHI]


TWO_SCENES_CONTENT = (
    "艾莉丝与诺克在酒馆交谈。\n"
    "「你好。」艾莉丝说。\n"
    "「好久不见。」诺克说。\n"
    "两人走出酒馆来到街头。\n"
    "「这里的风好大。」艾莉丝说。\n"
    "「走吧。」诺克说。\n"
)


def _two_scenes_ir(with_events: bool = True):
    paragraphs = [
        {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-b", "emotion": "serious",
         "stage_events": [{"type": "exit", "character_id": "char-b"}] if with_events else []},
        {"paragraph_id": "paragraph-0004", "scene_key": "s2", "kind": "narration"},
        {"paragraph_id": "paragraph-0005", "scene_key": "s2", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0006", "scene_key": "s2", "kind": "dialogue",
         "speaker_character_id": "char-b", "emotion": "serious"},
    ]
    return _ir(
        {"paragraphs": paragraphs,
         "scenes": [
             {"scene_key": "s1", "title": "酒馆", "location": "酒馆"},
             {"scene_key": "s2", "title": "街头", "location": "街头"},
         ]},
        TWO_SCENES_CONTENT,
    )


def _two_scenes_bindings():
    return [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("bg-2", "background", "paragraph-0004", "scene-002"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
        _binding("p-b-serious", "portrait", "paragraph-0003", "scene-001", "char-b", "serious"),
    ]


def test_positions_speaker_switch_keeps_other_on_stage():
    # 多人留台：char-b 接话时 char-a 不退场，双人队形（左/右），char-b 聚焦压暗 char-a
    ir = _two_scenes_ir(with_events=False)
    graph = _compile(ir, _two_scenes_bindings()).graph
    flat = _flat_actions(graph)
    para2 = flat[3]
    para3 = flat[4]
    tachi_a = next(node for node in para2 if node["SubType"] == SUBTYPE_TACHI)
    assert tachi_a["Data"]["TargetPosition"] == {
        "Kind": "Vector2", "X": _anchor_x(1, "center"), "Y": STAGE_PORTRAIT_TOP_Y,
    }
    assert [node for node in para3 if node["SubType"] == SUBTYPE_TACHI_EXIT] == []
    move_a = [node for node in para3 if node["SubType"] == SUBTYPE_TACHI_MOVE]
    assert [n["Data"]["TachiID"]["StringValue"] for n in move_a] == ["char-a"]
    assert move_a[0]["Data"]["TargetPosition"]["X"] == _anchor_x(2, "left")
    tachi_b = next(node for node in para3 if node["SubType"] == SUBTYPE_TACHI)
    assert tachi_b["Data"]["TachiID"]["StringValue"] == "char-b"
    assert tachi_b["Data"]["TargetPosition"]["X"] == _anchor_x(2, "right")
    highlight = next(node for node in para3 if node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT)
    assert highlight["Data"]["TachiID"]["StringValue"] == "char-b"


def test_scene_change_clears_stage_before_background_crossfade():
    # 多人留台：para3 说话人切换不退场，char-a/char-b 都留场，换景段才清场
    ir = _two_scenes_ir(with_events=False)
    graph = _compile(ir, _two_scenes_bindings()).graph
    flat = _flat_actions(graph)
    para3 = flat[4]
    para4 = flat[5]
    assert SUBTYPE_TACHI_EXIT not in [node["SubType"] for node in para3]
    # 换景段落：清场先于背景（同帧触发序）
    subtypes = [node["SubType"] for node in para4]
    assert SUBTYPE_CLEAR_ALL_TACHIS in subtypes
    assert SUBTYPE_BACKGROUND in subtypes
    assert subtypes.index(SUBTYPE_CLEAR_ALL_TACHIS) < subtypes.index(SUBTYPE_BACKGROUND)
    background = next(node for node in para4 if node["SubType"] == SUBTYPE_BACKGROUND)
    assert background["Data"]["ChangeType"]["StringValue"] == "CrossFade"


def test_speaker_focus_highlight_only_on_switch():
    ir = _two_scenes_ir(with_events=False)
    graph = _compile(ir, _two_scenes_bindings()).graph
    flat = _flat_actions(graph)
    para2 = flat[3]
    para3 = flat[4]
    # 第一句聚焦艾莉丝
    hl2 = [node for node in para2 if node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT]
    assert len(hl2) == 1
    assert hl2[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert hl2[0]["Data"]["DimAlpha"]["NumberValue"] == STAGE_DIM_ALPHA
    # 换说话人聚焦诺克；旁白段不重发
    hl3 = [node for node in para3 if node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT]
    assert len(hl3) == 1
    assert hl3[0]["Data"]["TachiID"]["StringValue"] == "char-b"


CG_CONTENT = (
    "艾莉丝与诺克在酒馆交谈。\n"
    "「你还记得那年吗？」艾莉丝说。\n"
    "回忆的画面涌上心头。\n"
    "「记得。」诺克轻声说。\n"
)


def test_cg_lifecycle_clears_and_restores_stage():
    paragraphs = [
        {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration",
         "keyframe": {"required": True, "prompt": "回忆CG"}},
        {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-b", "emotion": "serious"},
    ]
    ir = _ir({"paragraphs": paragraphs, "scenes": [{"scene_key": "s1"}]}, CG_CONTENT)
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
        _binding("p-b-serious", "portrait", "paragraph-0004", "scene-001", "char-b", "serious"),
        _binding("kf-1", "keyframe", "paragraph-0003", "scene-001"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    para3 = flat[4]
    para4 = flat[5]
    art = [node for node in para3 if node["SubType"] == SUBTYPE_ART]
    assert len(art) == 1
    assert para3[0]["SubType"] == SUBTYPE_ART  # CG 段直接 ART（前端隐式清屏）
    # CG 后首段：先清插画，再恢复在场人物
    assert para4[0]["SubType"] == SUBTYPE_CLEAR_ILLUSTRATION
    clear = para4[0]
    assert clear["Data"]["ClearType"]["StringValue"] == "Instant"
    restored = [node for node in para4 if node["SubType"] == SUBTYPE_TACHI]
    # 多人留台：CG 结束按账本恢复 char-a，随后新说话人 char-b 入场
    assert [n["Data"]["TachiID"]["StringValue"] for n in restored] == ["char-a", "char-b"]
    assert any(node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT for node in para4)


def test_display_name_used_for_speaker_id():
    ir = _single_scene_ir(
        {"paragraph-0002": {"speaker_display_name": "？？？"}}
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
    ]
    graph = _compile(ir, bindings).graph
    nodes_by_index = {node["Index"]: node for node in graph["Nodes"]}
    line = nodes_by_index[3]["Data"]["Lines"]["Items"][0]["ObjectValue"]
    assert line["SpeakerId"]["StringValue"] == "？？？"
    line4 = nodes_by_index[4]["Data"]["Lines"]["Items"][0]["ObjectValue"]
    assert line4["SpeakerId"]["StringValue"] == "艾莉丝"  # 无 display_name 回退真名


def test_v1_ir_degrades_gracefully():
    ir = _two_scenes_ir(with_events=False)
    ir["schema_version"] = "script-ir-v1"
    result = _compile(ir, _two_scenes_bindings())
    graph = result.graph
    assert graph["Meta"]["compiler"]["compiler_version"] == VNGRAPH_COMPILER_VERSION
    flat = _flat_actions(graph)
    para4 = flat[5]
    # v1 无 stage_events：退场靠换景清场兜底
    assert SUBTYPE_CLEAR_ALL_TACHIS in [node["SubType"] for node in para4]
    assert not any(node["SubType"] == SUBTYPE_TACHI_EXIT for node in para4)


def test_old_per_paragraph_slot_layout_does_not_flicker():
    ir = _single_scene_ir()
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-1", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
        _binding("p-2", "portrait", "paragraph-0003", "scene-001", "char-a", "happy",
                 url="https://cdn.example/p-1.png"),
        _binding("p-3", "portrait", "paragraph-0004", "scene-001", "char-a", "happy",
                 url="https://cdn.example/p-1.png"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    tachi_nodes = [
        node
        for actions in flat.values()
        for node in actions
        if node["SubType"] == SUBTYPE_TACHI
    ]
    change_nodes = [
        node
        for actions in flat.values()
        for node in actions
        if node["SubType"] == SUBTYPE_TACHI_CHANGE_IMAGE
    ]
    assert len(tachi_nodes) == 1
    assert change_nodes == []


def test_compile_is_deterministic_and_meta_reports_performance():
    ir = _two_scenes_ir()
    bindings = _two_scenes_bindings()
    first = _compile(ir, bindings)
    second = _compile(ir, bindings)
    assert first.graph_hash == second.graph_hash
    meta = first.graph["Meta"]
    assert meta["performance"]["tachi_enter"] >= 2
    assert meta["performance"]["background"] == 2
    # with_events 版：para3 = char-b 同段说完退场（exit 事件）；多人留台下
    # 说话人切换不再产生结算退场，退场只由 exit 事件/换景驱动
    assert meta["performance"]["tachi_exit"] == 1
    assert meta["stage_warnings"] == []


def test_scene_two_speaker_reenters_after_scene_change():
    # 章节级槽去重后立绘锚点只在场景1首现段；场景2说话人必须靠自动入场兜底
    ir = _two_scenes_ir(with_events=False)
    graph = _compile(ir, _two_scenes_bindings()).graph
    flat = _flat_actions(graph)
    para5 = flat[6]
    para6 = flat[7]
    enter5 = [node for node in para5 if node["SubType"] == SUBTYPE_TACHI]
    assert len(enter5) == 1
    assert enter5[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert enter5[0]["Data"]["TargetPosition"]["X"] == _anchor_x(1, "center")
    enter6 = [node for node in para6 if node["SubType"] == SUBTYPE_TACHI]
    assert len(enter6) == 1
    assert enter6[0]["Data"]["TachiID"]["StringValue"] == "char-b"
    # 场景2换说话人重新聚焦
    hl6 = [node for node in para6 if node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT]
    assert len(hl6) == 1 and hl6[0]["Data"]["TachiID"]["StringValue"] == "char-b"


def test_cg_then_scene_change_clears_illustration_without_resurrection():
    ir = _ir(
        {
            "paragraphs": [
                {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
                {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "narration",
                 "keyframe": {"required": True, "prompt": "回忆CG"}},
                {"paragraph_id": "paragraph-0003", "scene_key": "s2", "kind": "narration"},
                {"paragraph_id": "paragraph-0004", "scene_key": "s2", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
            ],
            "scenes": [
                {"scene_key": "s1", "title": "酒馆"},
                {"scene_key": "s2", "title": "街头"},
            ],
        },
        "「你还记得那年吗？」艾莉丝说。\n回忆的画面涌上心头。\n两人走出酒馆来到街头。\n「这里的风好大。」艾莉丝说。\n",
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("bg-2", "background", "paragraph-0003", "scene-002"),
        _binding("p-a-happy", "portrait", "paragraph-0001", "scene-001", "char-a", "happy"),
        _binding("kf-1", "keyframe", "paragraph-0002", "scene-001"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    para3 = flat[4]
    para4 = flat[5]
    # 换景段：先清插画（否则全屏 CG 盖住背景叠化），再切背景；不复活旧场人物
    assert para3[0]["SubType"] == SUBTYPE_CLEAR_ILLUSTRATION
    subtypes3 = [node["SubType"] for node in para3]
    assert subtypes3.index(SUBTYPE_CLEAR_ILLUSTRATION) < subtypes3.index(SUBTYPE_BACKGROUND)
    assert not [node for node in para3 if node["SubType"] == SUBTYPE_TACHI]
    # 场景2说话人重新入场（自动兜底），不携带场景1旧槽位
    enter4 = [node for node in para4 if node["SubType"] == SUBTYPE_TACHI]
    assert len(enter4) == 1
    assert enter4[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert enter4[0]["Data"]["TargetPosition"]["X"] == _anchor_x(1, "center")


def test_consecutive_cg_paragraphs_restore_pre_cg_stage():
    ir = _ir(
        {
            "paragraphs": [
                {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
                {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "narration",
                 "keyframe": {"required": True, "prompt": "CG上"}},
                {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration",
                 "keyframe": {"required": True, "prompt": "CG下"}},
                {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
            ],
            "scenes": [{"scene_key": "s1"}],
        },
        "「你还记得那年吗？」艾莉丝说。\n画面定格。\n画面继续。\n「我记得。」艾莉丝说。\n",
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0001", "scene-001", "char-a", "happy"),
        _binding("kf-1", "keyframe", "paragraph-0002", "scene-001"),
        _binding("kf-2", "keyframe", "paragraph-0003", "scene-001"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    para2 = flat[3]
    para3 = flat[4]
    para4 = flat[5]
    # CG 段落不再先发立绘/聚焦节点（会被 ART 同帧清空）
    assert [node["SubType"] for node in para2] == [SUBTYPE_ART]
    assert [node["SubType"] for node in para3] == [SUBTYPE_ART]
    # 双 CG 结束后一次性恢复 CG 前在场人物
    assert para4[0]["SubType"] == SUBTYPE_CLEAR_ILLUSTRATION
    restored = [node for node in para4 if node["SubType"] == SUBTYPE_TACHI]
    assert len(restored) == 1
    assert restored[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert restored[0]["Data"]["TachiIamge"]["StringValue"].endswith("p-a-happy.png")


ENTER_EXIT_CONTENT = (
    "「你还记得那年吗？」艾莉丝说。\n"
    "「我该走了。」艾莉丝说完转身离开，诺克走上前。\n"
    "「放心吧。」诺克说。\n"
    "「交给我。」诺克又说。\n"
)


def test_enter_and_exit_same_paragraph_allocates_center():
    ir = _ir(
        {
            "paragraphs": [
                {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
                {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy",
                 "stage_events": [{"type": "enter", "character_id": "char-b"},
                                  {"type": "exit", "character_id": "char-a"}]},
                {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-b", "emotion": "serious"},
                {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-b", "emotion": "serious"},
            ],
            "scenes": [{"scene_key": "s1", "title": "酒馆"}],
        },
        ENTER_EXIT_CONTENT,
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0001", "scene-001", "char-a", "happy"),
        _binding("p-b-serious", "portrait", "paragraph-0003", "scene-001", "char-b", "serious"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    para2 = flat[3]
    para3 = flat[4]
    # char-a 说完即退场；多人留台下非说话人 enter 事件当场入场，
    # 退场者让位后 char-b 独占中槽，para3 转为聚焦不再入场
    exit_nodes = [node for node in para2 if node["SubType"] == SUBTYPE_TACHI_EXIT]
    assert len(exit_nodes) == 1
    assert exit_nodes[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    enter_b = next(
        node for node in para2 if node["SubType"] == SUBTYPE_TACHI
        and node["Data"]["TachiID"]["StringValue"] == "char-b"
    )
    assert enter_b["Data"]["TargetPosition"]["X"] == _anchor_x(1, "center")
    assert not [
        node for node in para3 if node["SubType"] == SUBTYPE_TACHI
        and node["Data"]["TachiID"]["StringValue"] == "char-b"
    ]
    assert any(
        node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT
        and node["Data"]["TachiID"]["StringValue"] == "char-b"
        for node in para3
    )


def test_same_speaker_consecutive_paragraphs_stay_on_stage():
    # 同说话人连续对白段不重复进退场；中间夹旁白段也保持在场
    ir = _ir(
        {
            "paragraphs": [
                {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
                {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
                {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration"},
                {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
            ],
            "scenes": [{"scene_key": "s1", "title": "酒馆"}],
        },
        "「你好。」艾莉丝说。\n「随便看看。」艾莉丝继续说。\n她环顾四周。\n「再来一句。」艾莉丝还说。\n",
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0001", "scene-001", "char-a", "happy"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    assert SUBTYPE_TACHI_EXIT not in [
        node["SubType"] for nodes in flat.values() for node in nodes
    ]
    tachi_nodes = [
        node for nodes in flat.values() for node in nodes if node["SubType"] == SUBTYPE_TACHI
    ]
    assert len(tachi_nodes) == 1


def test_narration_ignores_display_name():
    ir = _single_scene_ir(
        {
            "paragraph-0001": {"speaker_display_name": "？？？"},
        }
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
    ]
    graph = _compile(ir, bindings).graph
    nodes_by_index = {node["Index"]: node for node in graph["Nodes"]}
    narration = nodes_by_index[2]["Data"]["Lines"]["Items"][0]["ObjectValue"]
    # 前端契约：旁白/叙述段 SpeakerId 填空串，不显示名字
    assert narration["SpeakerId"]["StringValue"] == ""
    dialogue = nodes_by_index[3]["Data"]["Lines"]["Items"][0]["ObjectValue"]
    assert dialogue["CharacterId"]["StringValue"] == "char-a"


def test_unregistered_speaker_keeps_display_name():
    # 不在 bible 角色表中的说话人：对白保留，CharacterId 空但 SpeakerId 显示称呼
    ir = _ir(
        {
            "paragraphs": [
                {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": None, "speaker_name": "刘洋",
                 "speaker_display_name": "刘洋", "unregistered_speaker": True,
                 "emotion": "neutral"},
                {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "narration"},
            ],
            "scenes": [{"scene_key": "s1", "title": "实验室"}],
        },
        "「一鸣，还没走呢？」刘洋推门进来。\n夜深了。\n",
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
    ]
    graph = _compile(ir, bindings).graph
    nodes_by_index = {node["Index"]: node for node in graph["Nodes"]}
    line = nodes_by_index[2]["Data"]["Lines"]["Items"][0]["ObjectValue"]
    assert line["SpeakerId"]["StringValue"] == "刘洋"
    assert line["CharacterId"]["StringValue"] == ""
    assert "一鸣" in line["Text"]["StringValue"]


def test_orphan_keyframe_reanchors_with_warning():
    ir = _two_scenes_ir(with_events=False)
    bindings = [
        *_two_scenes_bindings(),
        _binding("kf-orphan", "keyframe", "paragraph-9999", "scene-001"),
    ]
    result = _compile(ir, bindings)
    graph = result.graph
    assert any("paragraph-9999" in warning for warning in graph["Meta"]["stage_warnings"])
    # 重锚到首段：stage 空场时播 CG，不产生舞台快照/恢复副作用
    flat = _flat_actions(graph)
    para1 = flat.get(2, [])
    assert any(node["SubType"] == SUBTYPE_ART for node in para1)


def test_chapter_ending_on_cg_reports_warning():
    ir = _ir(
        {
            "paragraphs": [
                {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "dialogue",
                 "speaker_character_id": "char-a", "emotion": "happy"},
                {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "narration",
                 "keyframe": {"required": True, "prompt": "终幕CG"}},
            ],
            "scenes": [{"scene_key": "s1"}],
        },
        "「一切都结束了。」艾莉丝说。\n画面渐渐暗下。\n",
    )
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0001", "scene-001", "char-a", "happy"),
        _binding("kf-1", "keyframe", "paragraph-0002", "scene-001"),
    ]
    graph = _compile(ir, bindings).graph
    assert any("CG" in warning for warning in graph["Meta"]["stage_warnings"])


def test_tachi_enter_nodes_carry_provenance_keys():
    ir = _two_scenes_ir(with_events=False)
    graph = _compile(ir, _two_scenes_bindings()).graph
    enter_nodes = [
        node for node in graph["Nodes"]
        if node.get("NodeType") == 2 and node.get("SubType") == SUBTYPE_TACHI
    ]
    assert enter_nodes
    for node in enter_nodes:
        assert node.get("resource_slot_id")
        assert node.get("asset_version_id")
        assert node.get("resolved_media_url")
        assert node.get("paragraph_id")


NARRATION_CONTENT = (
    "夜色渐深。\n"
    "「我回来了。」艾莉丝说。\n"
    "烛火摇曳，屋内一片寂静。\n"
    "「你还在等谁？」诺克问。\n"
)


def _narration_ir():
    paragraphs = [
        {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-b", "emotion": "serious"},
    ]
    return _ir({"paragraphs": paragraphs, "scenes": [{"scene_key": "s1"}]}, NARRATION_CONTENT)


def _narration_bindings():
    return [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
        _binding("p-b-serious", "portrait", "paragraph-0004", "scene-001", "char-b", "serious"),
    ]


def test_narration_dims_stage_without_exit_or_enter():
    # 旁白伪角色：接棒段对台上人物发压暗 highlight，不入场不退场
    graph = _compile(_narration_ir(), _narration_bindings()).graph
    flat = _flat_actions(graph)
    para3 = flat[4]
    subtypes = [node["SubType"] for node in para3]
    assert SUBTYPE_TACHI not in subtypes
    assert SUBTYPE_TACHI_EXIT not in subtypes
    dims = [node for node in para3 if node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT]
    assert len(dims) == 1
    assert dims[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert dims[0]["Data"]["HighlightAlpha"]["NumberValue"] == STAGE_DIM_ALPHA


def test_same_speaker_refocuses_after_narration_without_reenter():
    # 旁白后同一说话人继续：仍在台上，仅重新聚焦，不重入
    paragraphs = [
        {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
    ]
    ir = _ir({"paragraphs": paragraphs, "scenes": [{"scene_key": "s1"}]}, NARRATION_CONTENT)
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    para4 = flat[5]
    subtypes = [node["SubType"] for node in para4]
    assert SUBTYPE_TACHI not in subtypes
    assert SUBTYPE_TACHI_EXIT not in subtypes
    focus = [node for node in para4 if node["SubType"] == SUBTYPE_TACHI_HIGHLIGHT]
    assert len(focus) == 1
    assert focus[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert focus[0]["Data"]["HighlightAlpha"]["NumberValue"] > STAGE_DIM_ALPHA


def test_narration_then_new_speaker_keeps_other_dimmed():
    # 旁白不驱散台上人物；换新说话人时旧角色留台被压暗，新说话人入场聚焦
    graph = _compile(_narration_ir(), _narration_bindings()).graph
    flat = _flat_actions(graph)
    para4 = flat[5]
    exits = [node for node in para4 if node["SubType"] == SUBTYPE_TACHI_EXIT]
    enters = [node for node in para4 if node["SubType"] == SUBTYPE_TACHI]
    assert exits == []
    assert [n["Data"]["TachiID"]["StringValue"] for n in enters] == ["char-b"]


def test_consecutive_narration_emits_dim_once():
    paragraphs = [
        {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "narration"},
    ]
    ir = _ir({"paragraphs": paragraphs, "scenes": [{"scene_key": "s1"}]}, NARRATION_CONTENT)
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    assert [node["SubType"] for node in flat.get(4, [])] .count(SUBTYPE_TACHI_HIGHLIGHT) == 1
    # 第二段连续旁白：无任何舞台节点
    assert flat.get(5, []) == []


def test_narrator_id_never_leaks_into_nodes():
    graph = _compile(_narration_ir(), _narration_bindings()).graph
    serialized = str(graph)
    assert "__narrator__" not in serialized


def test_cg_end_into_narration_restores_ledger_dimmed():
    # CG 结束后接旁白：账本全量恢复且压暗，不清不丢
    paragraphs = [
        {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration",
         "keyframe": {"required": True, "prompt": "回忆CG"}},
        {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0005", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
    ]
    content = NARRATION_CONTENT + "「都结束了。」艾莉丝说。\n"
    ir = _ir({"paragraphs": paragraphs, "scenes": [{"scene_key": "s1"}]}, content)
    bindings = [
        _binding("bg-1", "background", "paragraph-0001", "scene-001"),
        _binding("p-a-happy", "portrait", "paragraph-0002", "scene-001", "char-a", "happy"),
        _binding("kf-1", "keyframe", "paragraph-0003", "scene-001"),
    ]
    graph = _compile(ir, bindings).graph
    flat = _flat_actions(graph)
    para4 = flat[5]
    subtypes = [node["SubType"] for node in para4]
    # 恢复 char-a 且压暗
    assert SUBTYPE_CLEAR_ILLUSTRATION in subtypes
    assert SUBTYPE_TACHI in subtypes
    dims = [n for n in para4 if n["SubType"] == SUBTYPE_TACHI_HIGHLIGHT]
    assert len(dims) == 1
    assert dims[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    assert dims[0]["Data"]["HighlightAlpha"]["NumberValue"] == STAGE_DIM_ALPHA


def test_narration_with_exit_event_leaves_no_hanging_actor():
    # 旁白段带显式 exit：角色退场干净，narrator 不残留账本
    paragraphs = [
        {"paragraph_id": "paragraph-0001", "scene_key": "s1", "kind": "narration"},
        {"paragraph_id": "paragraph-0002", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-a", "emotion": "happy"},
        {"paragraph_id": "paragraph-0003", "scene_key": "s1", "kind": "narration",
         "stage_events": [{"type": "exit", "character_id": "char-a"}]},
        {"paragraph_id": "paragraph-0004", "scene_key": "s1", "kind": "dialogue",
         "speaker_character_id": "char-b", "emotion": "serious"},
    ]
    ir = _ir({"paragraphs": paragraphs, "scenes": [{"scene_key": "s1"}]}, NARRATION_CONTENT)
    graph = _compile(ir, _narration_bindings()).graph
    flat = _flat_actions(graph)
    para3 = flat[4]
    subtypes = [node["SubType"] for node in para3]
    assert SUBTYPE_TACHI_EXIT in subtypes
    exits = [n for n in para3 if n["SubType"] == SUBTYPE_TACHI_EXIT]
    assert exits[0]["Data"]["TachiID"]["StringValue"] == "char-a"
    # 后续段落 char-b 正常入场，不残留 char-a
    para4 = flat[5]
    enters = [n for n in para4 if n["SubType"] == SUBTYPE_TACHI]
    assert [n["Data"]["TachiID"]["StringValue"] for n in enters] == ["char-b"]

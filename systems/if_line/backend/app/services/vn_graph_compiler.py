"""Provider-free deterministic-v3 compiler from immutable Script IR."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.application.hashing import canonical_json, content_hash
from app.services.chapter_script_ir import (
    ScriptIRValidationError,
    normalize_emotion,
    validate_script_ir,
)
from app.utils.vn_graph_validator import ACTION_TACHI, ACTION_TACHI_CHANGE_IMAGE, ACTION_TACHI_EXIT, ACTION_TACHI_HIGHLIGHT, ACTION_TACHI_MOVE, ACTION_ART, ACTION_BACKGROUND, ACTION_CLEAR_ALL_TACHIS, ACTION_CLEAR_ILLUSTRATION, vn_graph_validator


VNGRAPH_SCHEMA_VERSION = "1"
VNGRAPH_COMPILER_VERSION = "deterministic-v3"
VNGRAPH_TACHI_POLICY_VERSION = "stage-ensemble-v2"
VNGRAPH_BINDING_MANIFEST_VERSION = "vngraph-binding-v2"

# 《AIVN 视觉小说表演规范》演出参数（§3 站位/聚焦）。
# 前端 TargetPosition 语义（TachiManager.GetDefaultPosition/Tachi.PlayEnter）：
# 立绘左上角坐标、1920×1080 设计空间、脚底基线对齐设计底边。
# 立绘渲染契约：全身立绘 portrait_full = 1024×1536（image_generation_service）；
# 但前端会把控件缩放到 0.85×设计高（VNStageLayout.TachiHeightRatio）再摆放，
# 所以有效占位是 1024×1536 × 0.6 = 612×918，站位按缩放后尺寸计算。
STAGE_DESIGN_WIDTH = 1920.0
STAGE_DESIGN_HEIGHT = 1080.0
STAGE_PORTRAIT_WIDTH = 1024.0
STAGE_PORTRAIT_HEIGHT = 1536.0
STAGE_TACHI_HEIGHT_RATIO = 0.85
STAGE_TACHI_BASE_WIDTH = STAGE_PORTRAIT_WIDTH * STAGE_TACHI_HEIGHT_RATIO * STAGE_DESIGN_HEIGHT / STAGE_PORTRAIT_HEIGHT
STAGE_TACHI_BASE_HEIGHT = STAGE_DESIGN_HEIGHT * STAGE_TACHI_HEIGHT_RATIO
STAGE_PORTRAIT_TOP_Y = STAGE_DESIGN_HEIGHT - STAGE_TACHI_BASE_HEIGHT
# 规范 §3：单人居中；双人左右位中心 650~730/1200~1300（取中值 690/1250）；
# 三人左中右层级展开。中心为立绘视觉中心，锚点按半宽换算成左上角。
STAGE_FORMATION_CENTERS: dict[int, dict[str, float]] = {
    1: {"center": 960.0},
    2: {"left": 690.0, "right": 1250.0},
    3: {"left": 480.0, "center": 960.0, "right": 1440.0},
}
STAGE_MAX_TRACKED = 3
STAGE_DIM_ALPHA = 0.63
STAGE_HIGHLIGHT_ALPHA = 1.0
STAGE_ENTER_DURATION = 0.3
STAGE_EXIT_DURATION = 0.3
STAGE_MOVE_DURATION = 0.3
STAGE_FOCUS_DURATION = 0.2
STAGE_BACKGROUND_DURATION = 0.5
STAGE_CG_DURATION = 0.5
STAGE_CG_CLEAR_DURATION = 0.3
# 旁白伪角色：仅存在于编译器焦点调度，绝不进入 on_stage 账本或任何节点 TachiID。
NARRATOR_ID = "__narrator__"


def stage_slot_anchor(count: int, slot: str) -> tuple[float, float]:
    """按在场人数的队形解析槽位左上角坐标（底对齐 + 视觉中心 − 半宽）。"""
    centers = STAGE_FORMATION_CENTERS[min(max(count, 1), STAGE_MAX_TRACKED)]
    center_x = centers[slot] if slot in centers else STAGE_FORMATION_CENTERS[1]["center"]
    return (round(center_x - STAGE_TACHI_BASE_WIDTH / 2, 2), STAGE_PORTRAIT_TOP_Y)


class VNGraphCompileError(ValueError):
    pass


@dataclass(frozen=True)
class VNGraphCompileInput:
    project_id: int
    display_index: int
    chapter_revision_id: str
    chapter_content_hash: str
    chapter_content: str
    script_revision_id: str
    script_hash: str
    script_ir: Mapping[str, Any]
    asset_bindings: Sequence[Mapping[str, Any]]
    voice_line_versions: Sequence[Mapping[str, Any]] = ()
    parent_revision_id: str | None = None
    schema_version: str = VNGRAPH_SCHEMA_VERSION
    compiler_version: str = VNGRAPH_COMPILER_VERSION
    tachi_policy_version: str = VNGRAPH_TACHI_POLICY_VERSION


@dataclass(frozen=True)
class VNGraphCompileResult:
    graph: dict[str, Any]
    graph_hash: str
    binding_manifest: dict[str, Any]
    binding_manifest_hash: str


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise VNGraphCompileError("VNGraph 编译输入必须是可序列化 JSON") from exc


def _text(value: Any) -> str:
    return str(value or "").strip()


def _role(item: Mapping[str, Any]) -> str:
    return _text(item.get("role")).lower().replace("-", "_")


def _asset_type(item: Mapping[str, Any]) -> str:
    return _text(item.get("asset_type")).lower().replace("-", "_")


def _media_url(item: Mapping[str, Any]) -> str:
    value = _text(item.get("media_url") or item.get("legacy_url"))
    return "" if value.startswith("res://") else value


def _asset_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        order = int(item.get("order_index") or 0)
    except (TypeError, ValueError) as exc:
        raise VNGraphCompileError("资源槽位 order_index 必须是整数") from exc
    return (
        _text(item.get("scene_id") or item.get("node_key")),
        _text(item.get("paragraph_id") or item.get("segment_key")),
        _text(item.get("character_id")),
        _role(item),
        order,
        _text(item.get("resource_slot_id") or item.get("binding_id")),
    )


def _voice_sort_key(item: Mapping[str, Any]) -> tuple[int, str]:
    try:
        order = int(item.get("order_index"))
    except (TypeError, ValueError) as exc:
        raise VNGraphCompileError("VoiceLine order_index 必须是整数") from exc
    return order, _text(item.get("id"))


def _string(value: Any) -> dict[str, Any]:
    return {"Kind": "String", "StringValue": str(value or "")}


def _int(value: int) -> dict[str, Any]:
    return {"Kind": "Int", "NumberValue": int(value)}


def _float(value: float) -> dict[str, Any]:
    return {"Kind": "Float", "NumberValue": float(value)}


def _enum(value: str) -> dict[str, Any]:
    return {"Kind": "Enum", "StringValue": value}


def _vector2(x: float, y: float) -> dict[str, Any]:
    return {"Kind": "Vector2", "X": float(x), "Y": float(y)}


def _null() -> dict[str, Any]:
    return {"Kind": "Null"}


def _list(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"Kind": "List", "Items": items}


def _object(value: dict[str, Any]) -> dict[str, Any]:
    return {"Kind": "Object", "ObjectValue": value}


def _node(
    index: int,
    *,
    display_name: str,
    node_type: int,
    sub_type: int,
    x: float,
    y: float,
    data: dict[str, Any],
    outputs: dict[str, list[int]],
) -> dict[str, Any]:
    return {
        "Index": index,
        "DisplayName": display_name,
        "Comment": "",
        "NodeType": node_type,
        "SubType": sub_type,
        "X": x,
        "Y": y,
        "Data": data,
        "Outputs": outputs,
    }


def _desired_slots(count: int) -> list[str]:
    if count <= 1:
        return ["center"]
    if count == 2:
        return ["left", "right"]
    return ["left", "center", "right"]


@dataclass
class _StageActor:
    character_id: str
    character_name: str
    media_url: str
    emotion_key: str
    slot: str
    position: tuple[float, float] = (0.0, 0.0)
    origin: dict[str, Any] = field(default_factory=dict)


@dataclass
class _PendingNode:
    display_name: str
    sub_type: int
    data: dict[str, Any]
    common: dict[str, Any] = field(default_factory=dict)


# CG 段落期间立绘类节点会被前端 ART 同帧清空，只做账本结算不发节点。
_STAGE_ONLY_KINDS = frozenset(
    {"tachi_enter", "tachi_move", "tachi_change_image", "tachi_highlight", "tachi_exit"}
)


class _StageDirector:
    """按段落顺序结算舞台状态并派生演出节点（表演规范 §2/§3/§4/§5/§7）。

    产物是 _PendingNode 序列，由调用方编号并挂到对应段落的 Actions。
    """

    def __init__(
        self,
        *,
        assets: Sequence[Mapping[str, Any]],
        paragraphs: Sequence[Mapping[str, Any]],
    ) -> None:
        self.warnings: list[str] = []
        self.counts: dict[str, int] = {}
        paragraph_ids = {_text(item.get("paragraph_id")) for item in paragraphs}
        first_paragraph_by_scene: dict[str, str] = {}
        for item in paragraphs:
            scene_id = _text(item.get("scene_id"))
            if scene_id and scene_id not in first_paragraph_by_scene:
                first_paragraph_by_scene[scene_id] = _text(item.get("paragraph_id"))

        self.background_anchors: dict[str, list[Mapping[str, Any]]] = {}
        self.portrait_anchors: dict[str, list[Mapping[str, Any]]] = {}
        self.keyframe_anchors: dict[str, list[Mapping[str, Any]]] = {}
        self.portrait_by_emotion: dict[tuple[str, str], Mapping[str, Any]] = {}
        self.portrait_any: dict[str, Mapping[str, Any]] = {}
        fallback_paragraph = _text(paragraphs[0].get("paragraph_id")) if paragraphs else ""

        for asset_order, asset in enumerate(assets):
            url = _media_url(asset)
            if not url:
                continue
            role = _role(asset) or _asset_type(asset)
            paragraph_id = _text(asset.get("paragraph_id") or asset.get("segment_key"))
            scene_id = _text(asset.get("scene_id") or asset.get("node_key"))
            if paragraph_id not in paragraph_ids:
                if role in {"background", "bg"} and scene_id in first_paragraph_by_scene:
                    paragraph_id = first_paragraph_by_scene[scene_id]
                else:
                    # 孤儿锚点统一重锚首段并告警；取模散落会把 CG/立绘挂到语义无关段落。
                    resource_label = _text(asset.get("resource_slot_id") or asset.get("binding_id"))
                    self._warn(
                        f"资源 {resource_label} 锚点段落 {paragraph_id or '空'} 不在 IR，重锚首段"
                    )
                    paragraph_id = fallback_paragraph
            if role in {"background", "bg"}:
                self.background_anchors.setdefault(paragraph_id, []).append(asset)
            elif role in {"portrait", "tachi", "character"}:
                self.portrait_anchors.setdefault(paragraph_id, []).append(asset)
                character_id = _text(asset.get("character_id"))
                if character_id:
                    emotion_key = normalize_emotion(asset.get("emotion")) or "neutral"
                    self.portrait_by_emotion.setdefault((character_id, emotion_key), asset)
                    self.portrait_any.setdefault(character_id, asset)
            elif role in {"keyframe", "cg"}:
                self.keyframe_anchors.setdefault(paragraph_id, []).append(asset)

        self.on_stage: dict[str, _StageActor] = {}
        self.current_scene_id = ""
        self.current_speaker = ""
        # CG 期间 on_stage 账本保留（前端 ART 已清屏但叙事上人物仍在场），
        # CG 结束段落按账本恢复；换景则丢弃账本走新场景。
        self.in_cg = False
        self._cg_mode = False

    def _emit(self, kind: str, node: _PendingNode) -> None:
        if self._cg_mode and kind in _STAGE_ONLY_KINDS:
            return
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self._pending.append(node)

    def _warn(self, message: str) -> None:
        self.warnings.append(message)

    def process(self, paragraph: Mapping[str, Any]) -> list[_PendingNode]:
        self._pending: list[_PendingNode] = []
        paragraph_id = _text(paragraph.get("paragraph_id"))
        scene_id = _text(paragraph.get("scene_id"))
        speaker = _text(paragraph.get("speaker_character_id")) or NARRATOR_ID
        emotion_key = normalize_emotion(paragraph.get("emotion")) or "neutral"
        scene_change = scene_id != self.current_scene_id
        is_cg_paragraph = paragraph_id in self.keyframe_anchors
        self._cg_mode = is_cg_paragraph

        if scene_change:
            # §2 换景必清旧场。CG 若尚未撤除先清插画（否则全屏 CG 盖住背景叠化），
            # 旧场景人物账本直接丢弃——新场景按本段锚点/说话人重新入场。
            was_in_cg = self.in_cg
            self.in_cg = False
            if was_in_cg:
                self._emit_clear_illustration(paragraph_id, scene_id)
            if self.on_stage and not was_in_cg:
                self._emit(
                    "clear_all_tachis",
                    _PendingNode(
                        display_name="清空立绘",
                        sub_type=ACTION_CLEAR_ALL_TACHIS,
                        data={},
                        common={"scene_id": scene_id, "paragraph_id": paragraph_id},
                    ),
                )
            self.on_stage = {}
            self.current_speaker = ""
            self.current_scene_id = scene_id
        elif self.in_cg and not is_cg_paragraph:
            # §7 CG 结束：清插画并按账本恢复在场人物（CG 段落中的退场者不恢复）。
            self.in_cg = False
            self._emit_clear_illustration(paragraph_id, scene_id)
            # 多人留台：CG 结束按账本恢复全部在场人物。
            self._apply_formation(paragraph_id=paragraph_id, emit_moves=False)
            for actor in self.on_stage.values():
                self._emit_tachi_node(actor, paragraph_id=paragraph_id, scene_id=scene_id)

        # 背景锚点段落直接发射（换景段落与场景内换背景如昼夜变化共用此路径）。
        for asset in self.background_anchors.get(paragraph_id, []):
            self._emit(
                "background",
                _PendingNode(
                    display_name="背景切换",
                    sub_type=ACTION_BACKGROUND,
                    data={
                        "BackgroundImage": _string(_media_url(asset)),
                        "ChangeType": _enum("CrossFade"),
                        "Duration": _float(STAGE_BACKGROUND_DURATION),
                        "SceneId": _string(scene_id),
                    },
                    common=self._common(asset, paragraph_id),
                ),
            )

        # 多人留台（stage-ensemble-v2）：新说话人接棒不退场其余角色，
        # 由 speaker-change 的 tachi_highlight 压暗他们（DimAlpha 有作用对象）；
        # 退场只由 stage_events.exit / 换景 / 超员逐出驱动。旁白不驱散台上人物。

        # 同段先记录退场名单：入场分配槽位时视为已离场，避免「移位后退场」的无效 MOVE。
        leaving = {
            _text(event.get("character_id"))
            for event in paragraph.get("stage_events") or []
            if isinstance(event, Mapping) and _text(event.get("type")).lower() == "exit"
        }

        # 多人留台：非本段说话人的锚点/enter 事件同样入场（压暗对象由此产生），
        # 立绘资源仍按锚点/首绑定解析，情绪用 neutral 池。
        entered: set[str] = set()
        for asset in self.portrait_anchors.get(paragraph_id, []):
            character_id = _text(asset.get("character_id"))
            if not character_id:
                self._warn(f"{paragraph_id} 立绘绑定缺少 character_id，已跳过")
                continue
            entered.add(character_id)
            self._apply_portrait(
                character_id,
                _text(asset.get("target_name")) or character_id,
                asset,
                paragraph_id=paragraph_id,
                scene_id=scene_id,
                exclude=leaving,
            )
        for event in paragraph.get("stage_events") or []:
            if not isinstance(event, Mapping):
                continue
            if _text(event.get("type")).lower() != "enter":
                continue
            character_id = _text(event.get("character_id"))
            if not character_id or character_id in self.on_stage or character_id in entered:
                continue
            entered.add(character_id)
            # 非说话人入场用其首个可用立绘（入场时机由叙事决定，与说话人情绪无关）。
            binding = self.portrait_any.get(character_id)
            if binding is None:
                self._warn(f"{paragraph_id} enter 事件角色 {character_id} 没有可用立绘绑定")
                continue
            self._apply_portrait(
                character_id,
                _text(binding.get("target_name")) or character_id,
                binding,
                paragraph_id=paragraph_id,
                scene_id=scene_id,
                exclude=leaving,
            )

        if speaker != NARRATOR_ID and speaker not in self.on_stage and speaker not in entered:
            binding = self._speaker_binding(speaker, emotion_key)
            if binding is not None:
                self._apply_portrait(
                    speaker,
                    _text(paragraph.get("speaker_name")) or speaker,
                    binding,
                    paragraph_id=paragraph_id,
                    scene_id=scene_id,
                    exclude=leaving - {speaker},
                )
                entered.add(speaker)
        elif speaker and speaker in self.on_stage:
            actor = self.on_stage[speaker]
            binding = self.portrait_by_emotion.get((speaker, emotion_key))
            if actor.emotion_key != emotion_key:
                if binding is not None:
                    self._change_image(
                        speaker, binding, paragraph_id=paragraph_id, scene_id=scene_id
                    )
                else:
                    self._warn(
                        f"角色 {speaker} 情绪 {emotion_key} 立绘未绑定，保持当前立绘"
                    )

        if speaker and speaker in self.on_stage and speaker != self.current_speaker:
            self._emit(
                "tachi_highlight",
                _PendingNode(
                    display_name=f"{self.on_stage[speaker].character_name or speaker}聚焦",
                    sub_type=ACTION_TACHI_HIGHLIGHT,
                    data={
                        "TachiID": _string(speaker),
                        "HighlightAlpha": _float(STAGE_HIGHLIGHT_ALPHA),
                        "DimAlpha": _float(STAGE_DIM_ALPHA),
                        "Duration": _float(STAGE_FOCUS_DURATION),
                    },
                    common={"scene_id": scene_id, "paragraph_id": paragraph_id},
                ),
            )
            self.current_speaker = speaker

        # 旁白伪角色接棒：台上人物集体压暗（镜头交给画外音），不入场不退场。
        # 连续旁白（current_speaker 已是 narrator）不重复发节点。
        if speaker == NARRATOR_ID and self.on_stage and self.current_speaker != NARRATOR_ID:
            for actor in list(self.on_stage.values()):
                self._emit(
                    "tachi_highlight",
                    _PendingNode(
                        display_name=f"{actor.character_name or actor.character_id}压暗",
                        sub_type=ACTION_TACHI_HIGHLIGHT,
                        data={
                            "TachiID": _string(actor.character_id),
                            "HighlightAlpha": _float(STAGE_DIM_ALPHA),
                            "DimAlpha": _float(STAGE_DIM_ALPHA),
                            "Duration": _float(STAGE_FOCUS_DURATION),
                        },
                        common={"scene_id": scene_id, "paragraph_id": paragraph_id},
                    ),
                )
            self.current_speaker = NARRATOR_ID

        # §2 退场挂在退场句之后：先完成本句入场与聚焦，再执行 stage_events 退场，
        # 保证「说完这句就走」的角色账本正确。
        for event in paragraph.get("stage_events") or []:
            character_id = _text(event.get("character_id")) if isinstance(event, Mapping) else ""
            event_type = _text(event.get("type")).lower() if isinstance(event, Mapping) else ""
            if event_type != "exit" or not character_id:
                continue
            self._exit_actor(character_id, paragraph_id=paragraph_id, scene_id=scene_id)

        if is_cg_paragraph:
            # §7 CG 生命周期：前端 ART 展示会隐式清空立绘；账本保留供 CG 后恢复
            #（CG 段落内的入场/退场已只做账本结算，不发会被同帧清空的节点）。
            for asset in self.keyframe_anchors.get(paragraph_id, []):
                self._emit(
                    "illustration",
                    _PendingNode(
                        display_name=f"插画: {_text(asset.get('target_name')) or paragraph_id}",
                        sub_type=ACTION_ART,
                        data={
                            "IllustrationImage": _string(_media_url(asset)),
                            "ChangeType": _enum("FadeIn"),
                            "Duration": _float(STAGE_CG_DURATION),
                            "ParagraphId": _string(paragraph_id),
                        },
                        common=self._common(asset, paragraph_id),
                    ),
                )
            self.in_cg = True
            self.current_speaker = ""

        return self._pending

    def _reflow(self, paragraph_id: str) -> None:
        # §3 退场后重排：剩余人物回到当前人数的标准队形。
        self._apply_formation(paragraph_id=paragraph_id)

    def _emit_clear_illustration(self, paragraph_id: str, scene_id: str) -> None:
        self._emit(
            "clear_illustration",
            _PendingNode(
                display_name="清除插画",
                sub_type=ACTION_CLEAR_ILLUSTRATION,
                data={
                    "ClearType": _enum("Instant"),
                    "Duration": _float(STAGE_CG_CLEAR_DURATION),
                },
                common={"scene_id": scene_id, "paragraph_id": paragraph_id},
            ),
        )

    def _speaker_binding(self, character_id: str, emotion_key: str) -> Mapping[str, Any] | None:
        binding = self.portrait_by_emotion.get((character_id, emotion_key))
        if binding is not None:
            return binding
        binding = self.portrait_any.get(character_id)
        if binding is not None:
            self._warn(f"角色 {character_id} 情绪 {emotion_key} 无专属立绘，回退首个绑定")
        return binding

    def _apply_portrait(
        self,
        character_id: str,
        character_name: str,
        asset: Mapping[str, Any],
        *,
        paragraph_id: str,
        scene_id: str,
        exclude: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        emotion_key = normalize_emotion(asset.get("emotion")) or "neutral"
        url = _media_url(asset)
        actor = self.on_stage.get(character_id)
        if actor is not None:
            if actor.emotion_key == emotion_key and actor.media_url == url:
                return
            self._change_image(character_id, asset, paragraph_id=paragraph_id, scene_id=scene_id)
            return
        if len(self.on_stage) >= STAGE_MAX_TRACKED:
            # 多人留台超员：逐出最久未活动者（账本按入场顺序，最早入场者让位）。
            evict = next(
                (cid for cid in self.on_stage if cid != character_id),
                None,
            )
            if evict is None:
                return
            self._warn(
                f"{paragraph_id} 在场角色超过 {STAGE_MAX_TRACKED}，"
                f"{self.on_stage[evict].character_name or evict} 让位退场"
            )
            self._exit_actor(evict, paragraph_id=paragraph_id, scene_id=scene_id)
        self._enter(
            character_id,
            character_name,
            url,
            emotion_key,
            paragraph_id=paragraph_id,
            scene_id=scene_id,
            origin=self._common(asset, paragraph_id),
            exclude=exclude,
        )

    def _change_image(
        self,
        character_id: str,
        asset: Mapping[str, Any],
        *,
        paragraph_id: str,
        scene_id: str,
    ) -> None:
        actor = self.on_stage.get(character_id)
        if actor is None:
            return
        url = _media_url(asset)
        actor.media_url = url
        actor.emotion_key = normalize_emotion(asset.get("emotion")) or "neutral"
        actor.origin = self._common(asset, paragraph_id)
        self._emit(
            "tachi_change_image",
            _PendingNode(
                display_name=f"{actor.character_name or character_id}换表情",
                sub_type=ACTION_TACHI_CHANGE_IMAGE,
                data={
                    "TachiID": _string(character_id),
                    "TachiIamge": _string(url),
                },
                common=self._common(asset, paragraph_id),
            ),
        )

    def _apply_formation(
        self,
        *,
        paragraph_id: str,
        exclude: frozenset[str] | set[str] = frozenset(),
        joining: int = 0,
        emit_moves: bool = True,
    ) -> tuple[int, list[str]]:
        """按目标人数重排在场人物槽位与坐标。

        队形中心随人数变化（如双人左 690 → 三人左 480），槽名保留时也要校准坐标；
        emit_moves=False 用于 CG 恢复等人物不可见、直接落位的场景。
        """
        staying = [actor for cid, actor in self.on_stage.items() if cid not in exclude]
        new_count = len(staying) + joining
        desired = _desired_slots(new_count)
        taken = {actor.slot for actor in staying if actor.slot in desired}
        free = [slot for slot in desired if slot not in taken]
        for actor in staying:
            if actor.slot not in desired:
                actor.slot = free.pop(0)
        for actor in staying:
            target = stage_slot_anchor(new_count, actor.slot)
            if actor.position != target:
                actor.position = target
                if emit_moves:
                    self._emit(
                        "tachi_move",
                        _PendingNode(
                            display_name=f"{actor.character_name or actor.character_id}移位",
                            sub_type=ACTION_TACHI_MOVE,
                            data={
                                "TachiID": _string(actor.character_id),
                                "TargetPosition": _vector2(*target),
                                "Duration": _float(STAGE_MOVE_DURATION),
                            },
                            common={"paragraph_id": paragraph_id},
                        ),
                    )
        remaining_free = [slot for slot in desired if slot not in {a.slot for a in staying}]
        return new_count, remaining_free

    def _enter(
        self,
        character_id: str,
        character_name: str,
        url: str,
        emotion_key: str,
        *,
        paragraph_id: str,
        scene_id: str,
        origin: dict[str, Any] | None = None,
        exclude: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        # 槽位分配只统计「留下」的人物：同段退场者不参与目标队形（§3 退场即释放位置）。
        new_count, free = self._apply_formation(
            paragraph_id=paragraph_id, exclude=exclude, joining=1
        )
        slot = free[0] if free else "center"
        actor = _StageActor(
            character_id=character_id,
            character_name=character_name,
            media_url=url,
            emotion_key=emotion_key,
            slot=slot,
            position=stage_slot_anchor(new_count, slot),
            origin=dict(origin or {}),
        )
        self.on_stage[character_id] = actor
        self._emit_tachi_node(actor, paragraph_id=paragraph_id, scene_id=scene_id)

    def _exit_actor(
        self,
        character_id: str,
        *,
        paragraph_id: str,
        scene_id: str,
    ) -> None:
        """退场一个在场角色：发 tachi_exit、出账本并重排队形。CG 段落由 _emit 过滤只做账本结算。"""
        actor = self.on_stage.pop(character_id, None)
        if actor is None:
            return
        self._emit(
            "tachi_exit",
            _PendingNode(
                display_name=f"{actor.character_name or character_id}退场",
                sub_type=ACTION_TACHI_EXIT,
                data={
                    "TachiID": _string(character_id),
                    "ExitType": _enum("FadeOut"),
                    "Duration": _float(STAGE_EXIT_DURATION),
                },
                common={"scene_id": scene_id, "paragraph_id": paragraph_id},
            ),
        )
        if self.current_speaker == character_id:
            self.current_speaker = ""
        self._reflow(paragraph_id)

    def _emit_tachi_node(
        self,
        actor: _StageActor,
        *,
        paragraph_id: str,
        scene_id: str,
    ) -> None:
        # 溯源键（resource_slot_id 等）随账本携带；恢复/重挂场景下段落指向当前挂载段。
        common = {**actor.origin, "paragraph_id": paragraph_id, "scene_id": scene_id}
        self._emit(
            "tachi_enter",
            _PendingNode(
                display_name=f"{actor.character_name or actor.character_id}立绘",
                sub_type=ACTION_TACHI,
                data={
                    "TachiID": _string(actor.character_id),
                    "TachiIamge": _string(actor.media_url),
                    "TargetPosition": _vector2(*actor.position),
                    "EnterType": _enum("FadeIn"),
                    "Duration": _float(STAGE_ENTER_DURATION),
                    "Emotion": _string(actor.emotion_key),
                    "CharacterId": _string(actor.character_id),
                    "CharacterName": _string(actor.character_name),
                },
                common=common,
            ),
        )

    @staticmethod
    def _common(asset: Mapping[str, Any], paragraph_id: str) -> dict[str, Any]:
        return {
            "resource_slot_id": asset.get("resource_slot_id"),
            "asset_binding_id": asset.get("binding_id"),
            "asset_version_id": asset.get("asset_version_id"),
            "resolved_media_url": _media_url(asset),
            "paragraph_id": paragraph_id,
        }


class VNGraphCompiler:
    """Compile canonical VNGraph JSON without database or provider calls."""

    def validate_versions(self, source: VNGraphCompileInput) -> None:
        expected = {
            "schema_version": VNGRAPH_SCHEMA_VERSION,
            "compiler_version": VNGRAPH_COMPILER_VERSION,
            "tachi_policy_version": VNGRAPH_TACHI_POLICY_VERSION,
        }
        actual = {
            "schema_version": source.schema_version,
            "compiler_version": source.compiler_version,
            "tachi_policy_version": source.tachi_policy_version,
        }
        mismatches = [key for key, value in actual.items() if value != expected[key]]
        if mismatches:
            raise VNGraphCompileError("不支持的 VNGraph 编译版本: " + ", ".join(mismatches))

    def binding_manifest(self, source: VNGraphCompileInput) -> dict[str, Any]:
        self._validate_input(source)
        assets = _json_copy(list(source.asset_bindings))
        voices = _json_copy(list(source.voice_line_versions))
        assets.sort(key=_asset_sort_key)
        voices.sort(key=_voice_sort_key)
        return {
            "manifest_version": VNGRAPH_BINDING_MANIFEST_VERSION,
            "project_id": source.project_id,
            "display_index": source.display_index,
            "chapter_revision": {
                "id": source.chapter_revision_id,
                "content_hash": source.chapter_content_hash,
            },
            "script_revision": {
                "id": source.script_revision_id,
                "script_hash": source.script_hash,
            },
            "asset_bindings": assets,
            "voice_line_versions": voices,
            "versions": {
                "schema": source.schema_version,
                "compiler": source.compiler_version,
                "tachi_policy": source.tachi_policy_version,
            },
        }

    def compile(self, source: VNGraphCompileInput) -> VNGraphCompileResult:
        manifest = self.binding_manifest(source)
        manifest_hash = content_hash(manifest)
        graph = self._build_graph(
            source=source,
            assets=manifest["asset_bindings"],
            voices=manifest["voice_line_versions"],
            manifest_hash=manifest_hash,
        )
        self._validate_graph_structure(graph)
        if self._contains_res_url(graph):
            raise VNGraphCompileError("deterministic-v3 禁止生成 res:// 占位资源")
        validation = vn_graph_validator.validate_detailed(graph)
        if validation.errors:
            raise VNGraphCompileError("VNGraph 校验失败: " + "; ".join(validation.errors[:5]))
        canonical_graph = _json_copy(graph)
        return VNGraphCompileResult(
            graph=canonical_graph,
            graph_hash=content_hash(canonical_graph),
            binding_manifest=manifest,
            binding_manifest_hash=manifest_hash,
        )

    def _validate_input(self, source: VNGraphCompileInput) -> None:
        self.validate_versions(source)
        if source.project_id <= 0 or source.display_index <= 0:
            raise VNGraphCompileError("project_id 和 display_index 必须大于 0")
        if not _text(source.chapter_revision_id) or not _text(source.script_revision_id):
            raise VNGraphCompileError("chapter_revision_id 和 script_revision_id 不能为空")
        if content_hash(source.chapter_content) != source.chapter_content_hash:
            raise VNGraphCompileError("章节正文与 content_hash 不匹配")
        if content_hash(source.script_ir) != source.script_hash:
            raise VNGraphCompileError("Script IR 与 script_hash 不匹配")
        try:
            coverage = validate_script_ir(source.script_ir, expected_content=source.chapter_content)
        except ScriptIRValidationError as exc:
            raise VNGraphCompileError(str(exc)) from exc
        if coverage["coverage_ratio"] != 1.0:
            raise VNGraphCompileError("Script IR 正文覆盖率不是 100%")

        ids: set[str] = set()
        for item in source.asset_bindings:
            if not isinstance(item, Mapping):
                raise VNGraphCompileError("资源绑定 manifest 项必须是对象")
            binding_id = _text(item.get("resource_slot_id") or item.get("binding_id"))
            if not binding_id or binding_id in ids:
                raise VNGraphCompileError("资源绑定存在空或重复 ID")
            ids.add(binding_id)
            if _media_url(item) and (
                not _text(item.get("asset_version_id"))
                or not _text(item.get("asset_version_hash"))
            ):
                raise VNGraphCompileError(f"资源绑定未冻结 AssetVersion: {binding_id}")
            # Draft compilation may run while visual resources are still rendering.
            # Unresolved required slots remain in the immutable manifest and are
            # rejected later by publication readiness.
            if (
                bool(item.get("required"))
                and not _media_url(item)
                and _text(item.get("slot_status")) == "bound"
            ):
                raise VNGraphCompileError(f"已绑定的必需资源不可用: {binding_id}")

        occurrences: set[str] = set()
        orders: set[int] = set()
        for item in source.voice_line_versions:
            if not isinstance(item, Mapping):
                raise VNGraphCompileError("VoiceLineVersion manifest 项必须是对象")
            occurrence = _text(item.get("occurrence_id"))
            line_id = _text(item.get("id"))
            version_id = _text(item.get("voice_line_version_id"))
            order, _ = _voice_sort_key(item)
            if not line_id or not version_id or not occurrence or not _text(item.get("text")):
                raise VNGraphCompileError(
                    "VoiceLineVersion manifest 缺少版本 id、occurrence_id 或 text"
                )
            if not _text(item.get("voice_line_version_hash")):
                raise VNGraphCompileError("VoiceLineVersion manifest 缺少内容哈希")
            audio_version = item.get("audio_asset_version")
            if item.get("audio_asset_version_id") and (
                not isinstance(audio_version, Mapping)
                or not _text(audio_version.get("asset_version_hash"))
            ):
                raise VNGraphCompileError("VoiceLineVersion 未冻结音频 AssetVersion")
            if occurrence in occurrences or order in orders:
                raise VNGraphCompileError("VoiceLine occurrence_id 或 order_index 重复")
            occurrences.add(occurrence)
            orders.add(order)

    def _build_graph(
        self,
        *,
        source: VNGraphCompileInput,
        assets: Sequence[Mapping[str, Any]],
        voices: Sequence[Mapping[str, Any]],
        manifest_hash: str,
    ) -> dict[str, Any]:
        script = source.script_ir
        paragraphs = list(script.get("paragraphs") or [])
        paragraph_indices = {
            _text(item.get("paragraph_id")): index + 2 for index, item in enumerate(paragraphs)
        }
        end_index = len(paragraphs) + 2
        next_action_index = end_index + 1

        start = _node(
            1,
            display_name="开始",
            node_type=1,
            sub_type=6,
            x=80,
            y=80,
            data={},
            outputs={"Next": [2]},
        )
        progress_nodes: list[dict[str, Any]] = []
        action_nodes: list[dict[str, Any]] = []
        voices_by_text: dict[str, list[Mapping[str, Any]]] = {}
        for voice in voices:
            voices_by_text.setdefault(_text(voice.get("text")), []).append(voice)

        for order, paragraph in enumerate(paragraphs):
            paragraph_id = _text(paragraph.get("paragraph_id"))
            character_id = _text(paragraph.get("speaker_character_id"))
            speaker_name = _text(paragraph.get("speaker_name"))
            # 称谓（§5）：注册角色与未注册说话人都用玩家此刻应看到的称呼；
            # 旁白/叙述段 SpeakerId 按前端契约填空串（顶部不显示名字）。
            if character_id or paragraph.get("unregistered_speaker"):
                speaker_name = (
                    _text(paragraph.get("speaker_display_name")) or speaker_name
                )
            else:
                speaker_name = ""
            text = _text(paragraph.get("text"))
            voice_candidates = voices_by_text.get(text) or []
            voice = voice_candidates.pop(0) if voice_candidates else None
            audio_url = _text((voice or {}).get("audio_url"))
            line = {
                "SpeakerId": _string(speaker_name),
                "Text": _string(text),
                "VoiceId": _string((voice or {}).get("voice_profile_id") or ""),
                "AudioUrl": _string(audio_url) if audio_url else _null(),
                "VoiceLineId": _string((voice or {}).get("id")) if voice else _null(),
                "VoiceLineVersionId": (
                    _string((voice or {}).get("voice_line_version_id"))
                    if voice
                    else _null()
                ),
                "Emotion": _string(paragraph.get("emotion") or "neutral"),
                "CharacterId": _string(character_id),
                "ParagraphId": _string(paragraph_id),
                "SceneId": _string(paragraph.get("scene_id")),
                "SourceStart": _int(int(paragraph.get("source_start") or 0)),
                "SourceEnd": _int(int(paragraph.get("source_end") or 0)),
                "SourceText": _string(paragraph.get("source_text")),
            }
            node_index = paragraph_indices[paragraph_id]
            progress = _node(
                node_index,
                display_name="段落",
                node_type=1,
                sub_type=2,
                x=80 + (order % 3) * 350,
                y=230 + (order // 3) * 170,
                data={"Lines": _list([_object(line)])},
                outputs={
                    "Next": [
                        paragraph_indices[_text(paragraphs[order + 1].get("paragraph_id"))]
                        if order + 1 < len(paragraphs)
                        else end_index
                    ]
                },
            )
            progress["paragraph_id"] = paragraph_id
            progress["scene_id"] = _text(paragraph.get("scene_id"))
            progress["source_span_id"] = _text(paragraph.get("source_span_id"))
            progress_nodes.append(progress)

        def attach(paragraph_id: str, action: dict[str, Any]) -> None:
            target = next(item for item in progress_nodes if item["paragraph_id"] == paragraph_id)
            target["Outputs"].setdefault("Actions", []).append(action["Index"])
            action_nodes.append(action)

        paragraph_coords = {
            item["paragraph_id"]: (float(item["X"]), float(item["Y"])) for item in progress_nodes
        }
        director = _StageDirector(assets=assets, paragraphs=paragraphs)
        for paragraph in paragraphs:
            paragraph_id = _text(paragraph.get("paragraph_id"))
            base_x, base_y = paragraph_coords.get(paragraph_id, (80.0, 580.0))
            base_y += 350
            for action_offset, pending in enumerate(director.process(paragraph)):
                action = _node(
                    next_action_index,
                    display_name=pending.display_name,
                    node_type=2,
                    sub_type=pending.sub_type,
                    x=base_x + action_offset * 160,
                    y=base_y,
                    data=pending.data,
                    outputs={},
                )
                action.update(pending.common)
                attach(paragraph_id, action)
                next_action_index += 1

        keyframe_count = director.counts.get("illustration", 0)
        expected_keyframes = sum(
            1
            for item in assets
            if _media_url(item)
            and (_role(item) in {"keyframe", "cg"} or _asset_type(item) == "keyframe")
        )
        if keyframe_count != expected_keyframes:
            raise VNGraphCompileError("并非所有关键帧绑定都已挂载")
        # 前端 END 节点只弹结局卡不执行 Actions，章末 CG 只能保留显示，此处至少留痕。
        if director.in_cg:
            director.warnings.append("章节以 CG 段落收尾，插画保留至结局卡")
        annotation_gaps = [_text(pid) for pid in script.get("annotation_gaps") or []]
        if annotation_gaps:
            director.warnings.append(
                f"{len(annotation_gaps)} 个段落缺少 LLM 标注（回退默认场景/旁白）"
            )

        end = _node(
            end_index,
            display_name="章节结束",
            node_type=1,
            sub_type=11,
            x=80 + (len(paragraphs) % 3) * 350,
            y=230 + (len(paragraphs) // 3) * 170,
            data={},
            outputs={},
        )
        asset_manifest = {
            "backgrounds": self._manifest_group(assets, {"background", "bg"}),
            "portraits": self._manifest_group(assets, {"portrait", "tachi", "character"}),
            "keyframes": self._manifest_group(assets, {"keyframe", "cg"}),
            "voice_lines": [self._voice_manifest_item(item) for item in voices],
        }
        return {
            "Version": 1,
            "ProjectId": source.project_id,
            "ChapterIndex": source.display_index,
            "StartNodeIndex": 1,
            "Nodes": sorted([start, *progress_nodes, end, *action_nodes], key=lambda item: item["Index"]),
            "Meta": {
                "chapter_revision_id": source.chapter_revision_id,
                "script_revision_id": source.script_revision_id,
                "script_hash": source.script_hash,
                "binding_manifest_hash": manifest_hash,
                "compiler": {
                    "schema_version": source.schema_version,
                    "compiler_version": source.compiler_version,
                    "tachi_policy_version": source.tachi_policy_version,
                },
                "text_coverage": dict(script.get("coverage") or {}),
                "source_spans": _json_copy(script.get("spans") or []),
                "asset_manifest": asset_manifest,
                "keyframe_bindings_attached": keyframe_count,
                "performance": dict(director.counts),
                "stage_warnings": list(director.warnings),
            },
        }

    @staticmethod
    def _manifest_group(
        assets: Sequence[Mapping[str, Any]],
        roles: set[str],
    ) -> list[dict[str, Any]]:
        result = []
        for item in assets:
            if _role(item) not in roles and _asset_type(item) not in roles:
                continue
            result.append(
                {
                    "resource_slot_id": item.get("resource_slot_id"),
                    "binding_id": item.get("binding_id"),
                    "asset_version_id": item.get("asset_version_id"),
                    "storage_object_id": item.get("storage_object_id"),
                    "role": item.get("role"),
                    "scene_id": item.get("scene_id") or item.get("node_key"),
                    "paragraph_id": item.get("paragraph_id") or item.get("segment_key"),
                    "character_id": item.get("character_id"),
                    "order_index": item.get("order_index", 0),
                    "media_url": _media_url(item) or None,
                }
            )
        return result

    @staticmethod
    def _voice_manifest_item(line: Mapping[str, Any]) -> dict[str, Any]:
        audio_version = line.get("audio_asset_version")
        audio_version = audio_version if isinstance(audio_version, Mapping) else {}
        return {
            "id": line.get("id"),
            "voice_line_version_id": line.get("voice_line_version_id"),
            "voice_line_version_no": line.get("voice_line_version_no"),
            "occurrence_id": line.get("occurrence_id"),
            "order_index": line.get("order_index"),
            "audio_asset_version_id": line.get("audio_asset_version_id"),
            "storage_object_id": audio_version.get("storage_object_id"),
            "audio_url": line.get("audio_url"),
            "status": line.get("status"),
        }

    @staticmethod
    def _contains_res_url(value: Any) -> bool:
        if isinstance(value, str):
            return value.startswith("res://")
        if isinstance(value, Mapping):
            return any(VNGraphCompiler._contains_res_url(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(VNGraphCompiler._contains_res_url(item) for item in value)
        return False

    @staticmethod
    def _validate_graph_structure(graph: Mapping[str, Any]) -> None:
        nodes = graph.get("Nodes")
        if not isinstance(nodes, list) or not nodes:
            raise VNGraphCompileError("VNGraph 没有节点")
        indices = [node.get("Index") for node in nodes if isinstance(node, Mapping)]
        if len(indices) != len(nodes) or any(not isinstance(index, int) or index <= 0 for index in indices):
            raise VNGraphCompileError("VNGraph 节点 Index 无效")
        if len(indices) != len(set(indices)):
            raise VNGraphCompileError("VNGraph 节点 Index 重复")
        valid = set(indices)
        if graph.get("StartNodeIndex") not in valid:
            raise VNGraphCompileError("VNGraph StartNodeIndex 不存在")
        for node in nodes:
            outputs = node.get("Outputs") or {}
            if not isinstance(outputs, Mapping):
                raise VNGraphCompileError("VNGraph Outputs 必须是对象")
            for targets in outputs.values():
                if not isinstance(targets, list) or any(target not in valid for target in targets):
                    raise VNGraphCompileError("VNGraph Output 指向不存在的节点")


vn_graph_compiler = VNGraphCompiler()

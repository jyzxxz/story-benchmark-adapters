"""Pure construction and validation for immutable chapter Script IR."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from app.application.hashing import content_hash
from app.services.tts_service import TTS_MAX_TEXT_LENGTH as SEGMENT_MAX_TEXT_CHARS


SCRIPT_IR_SCHEMA_VERSION = "script-ir-v3"
SCRIPT_IR_SUPPORTED_SCHEMA_VERSIONS = ("script-ir-v1", "script-ir-v2", "script-ir-v3")
SCRIPT_GENERATOR_VERSION = "llm-segments-v1"


class ScriptIRValidationError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_emotion(value: Any) -> str:
    return " ".join(_text(value).lower().split())


def _normalize_stage_events(
    raw_events: Any,
    characters_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    if not isinstance(raw_events, list):
        return events
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        event_type = _text(raw.get("type")).lower()
        character_id = _text(raw.get("character_id"))
        if event_type not in {"enter", "exit"} or character_id not in characters_by_id:
            continue
        events.append({"type": event_type, "character_id": character_id})
    return events


def _character_id(name: str) -> str:
    return f"character-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]}"


def normalize_characters(raw_characters: Sequence[Any]) -> list[dict[str, str]]:
    by_id: dict[str, dict[str, str]] = {}
    for raw in raw_characters:
        if not isinstance(raw, Mapping):
            continue
        name = _text(raw.get("name") or raw.get("character_name"))
        if not name:
            continue
        character_id = _text(raw.get("character_id") or raw.get("id")) or _character_id(name)
        entry = {"character_id": character_id, "name": name}
        # bible 角色外观必须透传，否则立绘 prompt 无素材可扩写。
        appearance = _text(raw.get("appearance"))
        if appearance:
            entry["appearance"] = appearance
        # 二创角色 canonical 身份必须透传：立绘重写器的 subject_opening 依赖它
        # （"A teen male Obito Uchiha from Naruto"），丢失会导致画像不像原作。
        canonical = raw.get("canonical_identity")
        if isinstance(canonical, Mapping) and canonical.get("identity_prompt_en"):
            entry["canonical_identity"] = {
                key: canonical.get(key)
                for key in (
                    "canonical_name",
                    "franchise_name",
                    "identity_prompt_en",
                    "fixed_features",
                    "canonical_outfit",
                    "asymmetric_traits",
                    "look_variant",
                )
                if canonical.get(key)
            }
        by_id.setdefault(character_id, entry)
    return [by_id[key] for key in sorted(by_id)]


def _emotion_slug(emotion_key: str) -> str:
    # 超长情绪截断后追加哈希，避免两个共享 40 字符前缀的情绪撞 slot_key 唯一约束。
    if len(emotion_key) <= 40:
        return emotion_key
    digest = hashlib.sha256(emotion_key.encode("utf-8")).hexdigest()[:8]
    return f"{emotion_key[:32]}~{digest}"


# LLM segments 模式（script-ir-v3）：切分粒度由 LLM 定义，本模块只做
# 确定性守护——定位原文坐标、校验未改写/未漏覆盖、超长兜底拆分。
# --------------------------------------------------------------------- #

_SENTENCE_END_CHARS = "。！？!?；;…"


def _segment_text(segment: Mapping[str, Any], index: int) -> str:
    text = "".join(
        ch for ch in str(segment.get("text") or "") if not ch.isspace()
    )
    if not text:
        raise ScriptIRValidationError(f"segment[{index}] 文本为空")
    return text


def _split_overlong_segment(text: str) -> list[str]:
    """超长 segment 保险丝：优先句末标点切，找不到则硬切。仅兜底，正常不触发。"""
    if len(text) <= SEGMENT_MAX_TEXT_CHARS:
        return [text]
    pieces: list[str] = []
    remaining = text
    while len(remaining) > SEGMENT_MAX_TEXT_CHARS:
        window = remaining[:SEGMENT_MAX_TEXT_CHARS]
        cut = max(window.rfind(ch) for ch in _SENTENCE_END_CHARS)
        if cut < SEGMENT_MAX_TEXT_CHARS // 2:
            cut = SEGMENT_MAX_TEXT_CHARS - 1
        pieces.append(remaining[: cut + 1])
        remaining = remaining[cut + 1 :]
    if remaining:
        pieces.append(remaining)
    return pieces


def _locate_segments(
    content: str,
    segments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把 LLM 片段顺序流匹配回原文坐标。

    匹配语义：忽略一切空白（空格/换行/全角空格）后逐字符顺序匹配。
    失败即抛 ScriptIRValidationError，携带 segment 序号与具体原因。
    """
    stream = [(ch, idx) for idx, ch in enumerate(content) if not ch.isspace()]
    if not stream:
        raise ScriptIRValidationError("ChapterRevision 没有可编译内容")
    stream_text = "".join(ch for ch, _ in stream)
    cursor = 0
    located: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        text = _segment_text(segment, index)
        end_cursor = cursor + len(text)
        if end_cursor > len(stream_text):
            raise ScriptIRValidationError(
                f"segment[{index}] 在正文中定位失败：正文已耗尽"
                f"（疑似片段重复或顺序错乱），片段开头为「{text[:20]}」"
            )
        if stream_text[cursor:end_cursor] != text:
            expected = stream_text[cursor : cursor + len(text)]
            raise ScriptIRValidationError(
                f"segment[{index}] 与原文不一致（疑似改写/增删字/顺序错乱），"
                f"片段开头「{text[:20]}」，原文此处「{expected[:20]}」"
            )
        source_start = stream[cursor][1]
        source_end = stream[end_cursor - 1][1] + 1
        item = dict(segment)
        item["text"] = text
        item["source_start"] = source_start
        item["source_end"] = source_end
        located.append(item)
        cursor = end_cursor
    if cursor != len(stream_text):
        gap = stream_text[cursor : cursor + 20]
        raise ScriptIRValidationError(
            f"覆盖缺口：segments 之后正文还有 {len(stream_text) - cursor} 字未覆盖，"
            f"缺口开头「{gap}」"
        )
    return located


def _segment_source_spans(
    content: str,
    located: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """由定位结果生成逐字符无缝 spans（segment span + 分隔空白 separator span）。"""
    spans: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    span_index = 0
    cursor = 0
    for paragraph_index, item in enumerate(located, start=1):
        start = int(item["source_start"])
        end = int(item["source_end"])
        if start < cursor:
            raise ScriptIRValidationError("segment 坐标重叠")
        if start > cursor:
            span_index += 1
            spans.append(
                {
                    "span_id": f"span-{span_index:04d}",
                    "kind": "separator",
                    "start": cursor,
                    "end": start,
                    "text": content[cursor:start],
                }
            )
        span_index += 1
        span_id = f"span-{span_index:04d}"
        paragraph_id = f"paragraph-{paragraph_index:04d}"
        spans.append(
            {
                "span_id": span_id,
                "kind": "paragraph",
                "start": start,
                "end": end,
                "text": content[start:end],
                "paragraph_id": paragraph_id,
            }
        )
        paragraphs.append(
            {
                "paragraph_id": paragraph_id,
                "order_index": paragraph_index - 1,
                "source_span_id": span_id,
                "source_start": start,
                "source_end": end,
                "source_text": content[start:end],
                "text": str(item["text"]),
            }
        )
        cursor = end
    if cursor != len(content):
        span_index += 1
        spans.append(
            {
                "span_id": f"span-{span_index:04d}",
                "kind": "separator",
                "start": cursor,
                "end": len(content),
                "text": content[cursor:],
            }
        )
    return spans, paragraphs


def _annotate_paragraph(
    paragraph: dict[str, Any],
    annotation: Mapping[str, Any],
    *,
    characters_by_id: Mapping[str, Mapping[str, Any]],
    scene_registry: dict[str, str],
    scenes: list[dict[str, Any]],
    scene_metadata: Mapping[str, Mapping[str, Any]],
) -> None:
    """把 LLM 标注（kind/speaker/emotion/…）写进 paragraph，并登记场景。"""
    allowed_kinds = {"narration", "dialogue", "monologue", "action"}
    scene_key = _text(annotation.get("scene_key") or annotation.get("scene_id")) or "default"
    if scene_key not in scene_registry:
        scene_id = f"scene-{len(scene_registry) + 1:03d}"
        scene_registry[scene_key] = scene_id
        metadata = scene_metadata.get(scene_key, {})
        scenes.append(
            {
                "scene_id": scene_id,
                "order_index": len(scenes),
                "title": _text(metadata.get("title") or metadata.get("name")) or f"场景{len(scenes) + 1}",
                "location": _text(metadata.get("location")),
                "paragraph_ids": [],
            }
        )
    scene_id = scene_registry[scene_key]
    next(scene for scene in scenes if scene["scene_id"] == scene_id)["paragraph_ids"].append(
        paragraph["paragraph_id"]
    )

    kind = _text(annotation.get("kind")).lower()
    paragraph["kind"] = kind if kind in allowed_kinds else "narration"
    character_id = _text(
        annotation.get("speaker_character_id") or annotation.get("character_id")
    )
    if character_id not in characters_by_id:
        character_id = ""
    paragraph["scene_id"] = scene_id
    unregistered_speaker = kind in ("dialogue", "monologue") and not character_id
    paragraph["speaker_character_id"] = character_id or None
    paragraph["speaker_name"] = (
        characters_by_id[character_id]["name"]
        if character_id
        else (
            # 未注册说话人的对白保留 LLM 识别出的称呼，不再吞成旁白；
            # 回写 bible 后续章节即可引用到 character_id。
            _text(annotation.get("speaker_display_name"))
            or _text(annotation.get("speaker_name"))
            if unregistered_speaker
            else _text(annotation.get("speaker_name"))
        )
        or "旁白"
    )
    paragraph["unregistered_speaker"] = unregistered_speaker
    paragraph["speaker_display_name"] = (
        _text(annotation.get("speaker_display_name")) or None
    )
    paragraph["stage_events"] = _normalize_stage_events(
        annotation.get("stage_events"), characters_by_id
    )
    paragraph["emotion"] = _text(annotation.get("emotion")) or "neutral"
    keyframe = annotation.get("keyframe")
    keyframe_data = keyframe if isinstance(keyframe, Mapping) else {}
    paragraph["keyframe_required"] = bool(
        keyframe_data.get("required", annotation.get("keyframe_required", False))
    )
    paragraph["keyframe_prompt"] = _text(
        keyframe_data.get("prompt") or annotation.get("keyframe_prompt")
    )


def build_script_ir_from_segments(
    *,
    chapter_revision_id: str,
    chapter_content: str,
    chapter_content_hash: str,
    bible_revision_id: str,
    outline_revision_id: str,
    characters: Sequence[Any],
    llm_output: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """script-ir-v3：LLM 输出 segments（切分+标注），本函数只定位与校验。

    llm_output 契约：``{"segments": [{text, kind, speaker_character_id, ...}],
    "scenes": [{scene_key, title, location}]}``。segments 覆盖必须等于
    全文（忽略空白），否则抛 ScriptIRValidationError（调用方据此重试）。
    """
    if not chapter_content or content_hash(chapter_content) != chapter_content_hash:
        raise ScriptIRValidationError("ChapterRevision 正文为空或 content_hash 不匹配")

    raw_segments = (llm_output or {}).get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ScriptIRValidationError("LLM 输出缺少非空 segments 数组")
    for index, item in enumerate(raw_segments):
        if not isinstance(item, Mapping):
            raise ScriptIRValidationError(f"segment[{index}] 必须是对象")

    # 超长保险丝：>SEGMENT_MAX_TEXT_CHARS 的片段按句末标点确定性再拆。
    expanded: list[dict[str, Any]] = []
    for item in raw_segments:
        text = _segment_text(item, len(expanded))
        for piece in _split_overlong_segment(text):
            if piece == text:
                expanded.append(dict(item))
            else:
                child = dict(item)
                child["text"] = piece
                expanded.append(child)

    located = _locate_segments(chapter_content, expanded)
    spans, paragraphs = _segment_source_spans(chapter_content, located)

    normalized_characters = normalize_characters(characters)
    characters_by_id = {item["character_id"]: item for item in normalized_characters}

    raw_scenes = (llm_output or {}).get("scenes") or []
    scene_metadata: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw_scenes, list):
        for item in raw_scenes:
            if not isinstance(item, Mapping):
                continue
            key = _text(item.get("scene_key") or item.get("scene_id"))
            if key and key not in scene_metadata:
                scene_metadata[key] = item

    scene_registry: dict[str, str] = {}
    scenes: list[dict[str, Any]] = []
    for paragraph, located_item in zip(paragraphs, located):
        _annotate_paragraph(
            paragraph,
            located_item,
            characters_by_id=characters_by_id,
            scene_registry=scene_registry,
            scenes=scenes,
            scene_metadata=scene_metadata,
        )

    script_ir = {
        "schema_version": "script-ir-v3",
        "chapter_revision_id": chapter_revision_id,
        "bible_revision_id": bible_revision_id,
        "outline_revision_id": outline_revision_id,
        "source": {
            "content_hash": chapter_content_hash,
            "character_count": len(chapter_content),
        },
        "characters": normalized_characters,
        "spans": spans,
        "scenes": scenes,
        "paragraphs": paragraphs,
        # v3 无 annotation 步骤：漏段=覆盖缺口=构建失败，不再静默兜底。
        "annotation_gaps": [],
    }
    script_ir["coverage"] = validate_script_ir(script_ir, expected_content=chapter_content)
    return script_ir


def validate_script_ir(
    script_ir: Mapping[str, Any],
    *,
    expected_content: str | None = None,
) -> dict[str, Any]:
    if script_ir.get("schema_version") not in SCRIPT_IR_SUPPORTED_SCHEMA_VERSIONS:
        raise ScriptIRValidationError("不支持的 Script IR schema_version")
    spans = script_ir.get("spans")
    paragraphs = script_ir.get("paragraphs")
    scenes = script_ir.get("scenes")
    if not isinstance(spans, list) or not spans:
        raise ScriptIRValidationError("Script IR 缺少 source spans")
    if not isinstance(paragraphs, list) or not paragraphs:
        raise ScriptIRValidationError("Script IR 缺少 paragraphs")
    if not isinstance(scenes, list) or not scenes:
        raise ScriptIRValidationError("Script IR 缺少 scenes")

    cursor = 0
    reconstructed: list[str] = []
    paragraph_span_ids: set[str] = set()
    for span in spans:
        if not isinstance(span, Mapping):
            raise ScriptIRValidationError("Script IR source span 必须是对象")
        start = span.get("start")
        end = span.get("end")
        text = span.get("text")
        if not isinstance(start, int) or not isinstance(end, int) or not isinstance(text, str):
            raise ScriptIRValidationError("Script IR source span 字段无效")
        if start != cursor or end != start + len(text):
            raise ScriptIRValidationError("Script IR source span 不连续")
        if span.get("kind") == "paragraph":
            paragraph_span_ids.add(_text(span.get("span_id")))
        reconstructed.append(text)
        cursor = end
    source_text = "".join(reconstructed)
    if expected_content is not None and source_text != expected_content:
        raise ScriptIRValidationError("Script IR 未逐字符覆盖 ChapterRevision 原文")
    expected_hash = _text((script_ir.get("source") or {}).get("content_hash"))
    if content_hash(source_text) != expected_hash:
        raise ScriptIRValidationError("Script IR source content_hash 不匹配")

    paragraph_ids: set[str] = set()
    seen_span_ids: set[str] = set()
    scene_ids = {_text(scene.get("scene_id")) for scene in scenes if isinstance(scene, Mapping)}
    for paragraph in paragraphs:
        if not isinstance(paragraph, Mapping):
            raise ScriptIRValidationError("Script IR paragraph 必须是对象")
        paragraph_id = _text(paragraph.get("paragraph_id"))
        span_id = _text(paragraph.get("source_span_id"))
        scene_id = _text(paragraph.get("scene_id"))
        if not paragraph_id or paragraph_id in paragraph_ids:
            raise ScriptIRValidationError("Script IR paragraph_id 为空或重复")
        if span_id not in paragraph_span_ids or span_id in seen_span_ids:
            raise ScriptIRValidationError("Script IR paragraph 与 source span 不是一对一")
        if scene_id not in scene_ids:
            raise ScriptIRValidationError("Script IR paragraph 引用了不存在的 scene_id")
        paragraph_ids.add(paragraph_id)
        seen_span_ids.add(span_id)
    if seen_span_ids != paragraph_span_ids:
        raise ScriptIRValidationError("Script IR paragraph 未覆盖全部正文 source span")

    total = len(source_text)
    return {
        "mode": "llm-segments-v2" if script_ir.get("schema_version") == "script-ir-v3" else "exact-source-spans-v1",
        "total_characters": total,
        "covered_characters": total,
        "coverage_ratio": 1.0,
        "content_hash": content_hash(source_text),
        "paragraph_count": len(paragraphs),
        "span_count": len(spans),
    }


def resource_slot_specs(script_ir: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Allocate semantic slots without invoking any image provider."""
    validate_script_ir(script_ir)
    paragraphs = {
        _text(item.get("paragraph_id")): item
        for item in script_ir.get("paragraphs") or []
        if isinstance(item, Mapping)
    }
    characters_by_id = {
        _text(item.get("character_id")): item
        for item in script_ir.get("characters") or []
        if isinstance(item, Mapping) and _text(item.get("character_id"))
    }
    scenes = [item for item in script_ir.get("scenes") or [] if isinstance(item, Mapping)]
    specs: list[dict[str, Any]] = []

    for scene in scenes:
        scene_id = _text(scene.get("scene_id"))
        paragraph_ids = [pid for pid in scene.get("paragraph_ids") or [] if pid in paragraphs]
        if not paragraph_ids:
            continue
        first_paragraph_id = paragraph_ids[0]
        location = _text(scene.get("location") or scene.get("title")) or "章节场景"
        specs.append(
            {
                "slot_key": f"background:{scene_id}",
                "role": "background",
                "scene_id": scene_id,
                "paragraph_id": first_paragraph_id,
                "character_id": None,
                "required": True,
                "target_name": location,
                "prompt": f"视觉小说背景，{location}，无人，无文字",
            }
        )

    # 立绘槽章节级去重：同一「角色×情绪」整章只建一个槽，锚定首次出现段落
    #（规范 §4 同一情绪阶段保持同一表情；渲染层 _logical_key 本就是该粒度）。
    portrait_first_seen: dict[tuple[str, str], Mapping[str, Any]] = {}
    for paragraph in script_ir.get("paragraphs") or []:
        if not isinstance(paragraph, Mapping):
            continue
        character_id = _text(paragraph.get("speaker_character_id"))
        if not character_id:
            continue
        emotion_key = normalize_emotion(paragraph.get("emotion")) or "neutral"
        portrait_first_seen.setdefault((character_id, emotion_key), paragraph)

    for (character_id, emotion_key), paragraph in portrait_first_seen.items():
        emotion = _text(paragraph.get("emotion")) or "neutral"
        character = characters_by_id.get(character_id) or {}
        name = _text(paragraph.get("speaker_name")) or character.get("name") or character_id
        # 立绘 prompt 必须携带 bible 角色外观，否则重写器没有素材可扩写，
        # 产出会被 validate_portrait_final_prompt（>=40 字符）拒绝。
        # 二创角色优先 canonical 官方设计（英文 prompt），保证原作相似度。
        canonical = character.get("canonical_identity") or {}
        canonical_prompt = _text(canonical.get("identity_prompt_en"))
        appearance = _text(character.get("appearance"))
        portrait_prompt = (
            f"角色立绘，{name}（{canonical_prompt}），{emotion}，透明背景，无文字"
            if canonical_prompt
            else f"角色立绘，{name}（{appearance}），{emotion}，透明背景，无文字"
            if appearance
            else f"角色立绘，{name}，{emotion}，透明背景，无文字"
        )
        spec = {
            "slot_key": f"portrait:{character_id}:{_emotion_slug(emotion_key)}",
            "role": "portrait",
            "scene_id": _text(paragraph.get("scene_id")),
            "paragraph_id": _text(paragraph.get("paragraph_id")),
            "character_id": character_id,
            "required": True,
            "target_name": name,
            "prompt": portrait_prompt,
            "emotion": emotion,
        }
        if canonical:
            spec["canonical_identity"] = dict(canonical)
        specs.append(spec)

    # 场景在场补槽：整章未说话但实际在场的角色也应有立绘（主角常全程
    # 旁白但始终在台上）。按 scene 重放 stage_events(enter/exit)+说话人
    # 得出场景末仍留台的角色，对无任何说话人槽者补一个 neutral 槽，
    # 锚定其首次在场段落——编译器锚点路径按 paragraph_id 自然入场，
    # 后续 exit 事件照常清台。中途退场者不补。
    slotted_character_ids = {character_id for character_id, _emotion in portrait_first_seen}
    for scene in scenes:
        scene_id = _text(scene.get("scene_id"))
        present: set[str] = set()
        first_present: dict[str, str] = {}
        for pid in scene.get("paragraph_ids") or []:
            paragraph = paragraphs.get(pid)
            if paragraph is None:
                continue
            speaker = _text(paragraph.get("speaker_character_id"))
            if speaker and speaker in characters_by_id:
                present.add(speaker)
                first_present.setdefault(speaker, pid)
            for event in paragraph.get("stage_events") or []:
                if not isinstance(event, Mapping):
                    continue
                event_character = _text(event.get("character_id"))
                if not event_character or event_character not in characters_by_id:
                    continue
                if _text(event.get("type")) == "enter":
                    present.add(event_character)
                    first_present.setdefault(event_character, pid)
                elif _text(event.get("type")) == "exit":
                    present.discard(event_character)
        for character_id in sorted(present):
            if character_id in slotted_character_ids:
                continue
            slotted_character_ids.add(character_id)
            anchor_paragraph_id = first_present[character_id]
            character = characters_by_id.get(character_id) or {}
            name = _text(character.get("name")) or character_id
            canonical = character.get("canonical_identity") or {}
            canonical_prompt = _text(canonical.get("identity_prompt_en"))
            appearance = _text(character.get("appearance"))
            portrait_prompt = (
                f"角色立绘，{name}（{canonical_prompt}），neutral，透明背景，无文字"
                if canonical_prompt
                else f"角色立绘，{name}（{appearance}），neutral，透明背景，无文字"
                if appearance
                else f"角色立绘，{name}，neutral，透明背景，无文字"
            )
            spec = {
                "slot_key": f"portrait:{character_id}:neutral-scene",
                "role": "portrait",
                "scene_id": scene_id,
                "paragraph_id": anchor_paragraph_id,
                "character_id": character_id,
                "required": True,
                "target_name": name,
                "prompt": portrait_prompt,
                "emotion": "neutral",
            }
            if canonical:
                spec["canonical_identity"] = dict(canonical)
            specs.append(spec)

    for paragraph in script_ir.get("paragraphs") or []:
        if not isinstance(paragraph, Mapping):
            continue
        scene_id = _text(paragraph.get("scene_id"))
        paragraph_id = _text(paragraph.get("paragraph_id"))
        character_id = _text(paragraph.get("speaker_character_id"))
        if bool(paragraph.get("keyframe_required")):
            prompt = _text(paragraph.get("keyframe_prompt")) or _text(paragraph.get("text"))
            specs.append(
                {
                    "slot_key": f"keyframe:{scene_id}:{paragraph_id}",
                    "role": "keyframe",
                    "scene_id": scene_id,
                    "paragraph_id": paragraph_id,
                    "character_id": character_id or None,
                    "required": True,
                    "target_name": f"关键帧 {paragraph_id}",
                    "prompt": f"视觉小说剧情关键帧，{prompt}，无文字",
                }
            )

    for index, spec in enumerate(specs):
        spec["order_index"] = index
    return specs


# selfimprove 克隆本地兼容:旧迁移 0020 仍按旧名 import(空库不会实际调用)
build_script_ir = build_script_ir_from_segments


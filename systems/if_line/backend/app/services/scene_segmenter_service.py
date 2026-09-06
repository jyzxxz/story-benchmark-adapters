"""
Scene Segmenter Service

把章节正文切成多个视觉时刻。地点、时间、天气、关键物件、可见事件或
命名人物入场发生显著变化时，开始一个新的视觉时刻。

调用 LLM 做语义切分；失败时降级到单段（用 outline.scene 作为 location）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available
from app.services.text_llm_config import DEFAULT_TEXT_LLM_MODEL, resolve_request_model, text_llm_api_key, text_llm_base_url, text_llm_model

logger = logging.getLogger("scene_segmenter")

SCENE_SEGMENTER_ENABLED = os.getenv("SCENE_SEGMENTER_ENABLED", "true").lower() == "true"
SEGMENTER_MODEL = text_llm_model("SEGMENTER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
SEGMENTER_API_KEY = text_llm_api_key("SEGMENTER_API_KEY", "REWRITER_API_KEY")
SEGMENTER_BASE_URL = text_llm_base_url("SEGMENTER_BASE_URL", "REWRITER_BASE_URL")
SEGMENTER_TIMEOUT = float(os.getenv("SEGMENTER_TIMEOUT", "45.0"))
SEGMENTER_MAX_TOKENS = int(os.getenv("SEGMENTER_MAX_TOKENS", "4000"))
SEGMENTER_CONTENT_CHAR_LIMIT = int(os.getenv("SEGMENTER_CONTENT_CHAR_LIMIT", "8000"))
SEGMENTER_SINGLE_CONTENT_CHAR_LIMIT = int(os.getenv("SEGMENTER_SINGLE_CONTENT_CHAR_LIMIT", "900"))

# Cache and prompt contract version. Bumping this value prevents the old
# "one image per location" result from surviving after the visual-moment
# policy changes.
SCENE_SEGMENTER_VERSION = "content-visual-moment-v2"

# A chapter is divided into visual moments rather than unique locations. The
# cap still prevents sentence-by-sentence fragmentation, while allowing a
# normal 4k-8k character chapter to have enough visual variety.
MAX_SEGMENTS_PER_CHAPTER = int(os.getenv("SCENE_SEGMENTER_MAX_SEGMENTS", "16"))
TARGET_CHARS_PER_VISUAL_MOMENT = int(
    os.getenv("SCENE_SEGMENTER_TARGET_CHARS", "420")
)
# 当章节正文少于这个字数时，强制单段（不分）
MIN_CONTENT_LEN_FOR_SPLIT = 400


class SceneSegment(BaseModel):
    """单个场景段。"""

    segment_id: int = Field(description="段序号，从 1 开始")
    location: str = Field(description="该段的物理场景地点，中文短词，如 '朝堂' / '花园'")
    location_en: str = Field(default="", description="location 的英文翻译，用于图像生成")
    start_marker: str = Field(default="", description="该段开头的标志性短语（用于定位）")
    end_marker: str = Field(default="", description="该段结尾的标志性短语（用于定位）")
    summary: str = Field(default="", description="该段的简短中文摘要，<=50 字")
    characters_present: List[str] = Field(default_factory=list, description="该段出场角色名")
    speakers: List[str] = Field(default_factory=list, description="该段实际说话的命名角色")
    visible_actors: List[str] = Field(default_factory=list, description="该段实际执行可见动作的命名角色")
    mood: str = Field(default="day", description="该段氛围")
    time_of_day: str = Field(default="unknown", description="day/dawn/dusk/night/interior/unknown")
    event_signature: str = Field(
        default="",
        description="本段可被看见的事件变化，不含对白内容",
    )
    key_objects: List[str] = Field(
        default_factory=list,
        description="决定本段辨识度的环境物件或非人物事件",
    )
    visual_anchor: str = Field(
        default="",
        description="地点、时间、事件和关键物件组成的短视觉锚点",
    )
    scene_fingerprint: str = Field(
        default="",
        description="正文驱动的稳定场景指纹；同地点的不同事件不会合并",
    )
    environment_hint_en: str = Field(
        default="",
        description=(
            "该段纯环境视觉描述（英文），80-150 字符。"
            "仅含地点/建筑/家具/物体/光线/天气/氛围。"
            "绝对禁止任何人物/角色名/动作/对白/姿势描述。"
            "用于喂给 rewriter 生成背景图 prompt，避免剧情污染视觉模型。"
        ),
    )


@dataclass
class SegmenterStats:
    success: bool
    fallback: bool
    segment_count: int
    duration_s: float
    error: str = ""


class SceneSegmenterService:
    """
    章节场景分段服务。

    主要接口:
    - segment_chapter(chapter_content, chapter_outline) -> List[SceneSegment]
    - 失败/不可用时返回单段 (用 outline.scene)
    """

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._stats: List[SegmenterStats] = []
        self._cache: Dict[str, List["SceneSegment"]] = {}

    @staticmethod
    def _cache_key(chapter_content: str, chapter_outline: Dict[str, Any]) -> str:
        h = hashlib.md5()
        h.update(SCENE_SEGMENTER_VERSION.encode("utf-8"))
        h.update(b"|")
        h.update((chapter_content or "").encode("utf-8", errors="ignore"))
        h.update(b"|")
        h.update(str(chapter_outline.get("scene") or "").encode("utf-8", errors="ignore"))
        h.update(b"|")
        h.update(str(chapter_outline.get("summary") or "").encode("utf-8", errors="ignore"))
        return h.hexdigest()

    @staticmethod
    def _segment_limit(chapter_content: str) -> int:
        """Return a content-sized visual-moment budget.

        This is a soft upper bound for the LLM and a hard validation cap. It
        scales with prose length instead of forcing every story into 1-4
        reusable backgrounds.
        """
        content_length = len((chapter_content or "").strip())
        if content_length < MIN_CONTENT_LEN_FOR_SPLIT:
            return 1
        estimated = math.ceil(content_length / max(220, TARGET_CHARS_PER_VISUAL_MOMENT))
        return max(3, min(MAX_SEGMENTS_PER_CHAPTER, estimated))

    @staticmethod
    def _scene_fingerprint(segment: SceneSegment) -> str:
        payload = {
            "location": segment.location.strip(),
            "time_of_day": segment.time_of_day.strip(),
            "mood": segment.mood.strip(),
            "event_signature": segment.event_signature.strip(),
            "key_objects": [item.strip() for item in segment.key_objects if item.strip()],
            "visual_anchor": segment.visual_anchor.strip(),
            "start_marker": segment.start_marker.strip(),
            "summary": segment.summary.strip(),
            "environment_hint_en": segment.environment_hint_en.strip(),
            "version": SCENE_SEGMENTER_VERSION,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]

    @classmethod
    def _finalize_segments(
        cls,
        segments: List[SceneSegment],
        *,
        limit: int,
    ) -> List[SceneSegment]:
        """Normalize identities and drop only true duplicate visual moments.

        Location alone is deliberately not a deduplication key. Returning to
        the same room later, changing day to night, revealing a new prop, or
        starting a visible action are separate visual moments.
        """
        finalized: List[SceneSegment] = []
        previous_fingerprint = ""
        for segment in segments[:limit]:
            if not segment.visual_anchor:
                anchor_parts = [
                    segment.location,
                    segment.time_of_day if segment.time_of_day != "unknown" else segment.mood,
                    segment.event_signature,
                    "、".join(segment.key_objects[:4]),
                ]
                segment.visual_anchor = " · ".join(
                    part.strip() for part in anchor_parts if part and part.strip()
                )[:160]
            segment.scene_fingerprint = cls._scene_fingerprint(segment)
            # Only collapse an adjacent byte-for-byte semantic duplicate. A
            # later return to the same place is retained because start_marker
            # participates in the fingerprint.
            if segment.scene_fingerprint == previous_fingerprint:
                continue
            segment.segment_id = len(finalized) + 1
            finalized.append(segment)
            previous_fingerprint = segment.scene_fingerprint
        return finalized

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=SEGMENTER_API_KEY,
                pool_env=("SEGMENTER_API_KEYS", "REWRITER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=SEGMENTER_BASE_URL,
            )
        return self._client

    async def segment_chapter(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        force_single: bool = False,
    ) -> List[SceneSegment]:
        """
        把一章正文切成多个场景段。

        Args:
            chapter_content: 章节正文（中文）
            chapter_outline: 章节大纲 dict (含 scene/summary/characters/conflict/...)
            force_single: 强制单段（调试/降级用）

        Returns:
            SceneSegment 列表。至少 1 个段。
        """
        # 内存缓存：同一章节内容+outline 重复切分直接命中，避免重复调 LLM。
        # vngraph 生成与 generate_chapter_backgrounds_v2 都会调本服务，缓存可省掉第二次 ~3-5s。
        cache_key = self._cache_key(chapter_content, chapter_outline)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info("segmenter cache hit key=%s segments=%d", cache_key[:8], len(cached))
            return cached

        # outline.scene 可能是 None（DB 里某些章节没填），用 summary 兜底
        outline_scene = chapter_outline.get("scene") or ""
        if not outline_scene:
            # 用 summary 的前 30 字当 fallback scene，避免落到 "未知场景" 这种空壳
            summary = (chapter_outline.get("summary") or "").strip()
            outline_scene = summary[:30] if summary else "default scene"
        outline_chars = chapter_outline.get("characters", []) or []
        outline_mood = chapter_outline.get("emotion") or "day"

        # 早退条件：API key 缺失才真的降级（不调 LLM）
        if not SCENE_SEGMENTER_ENABLED or not api_key_available(
            SEGMENTER_API_KEY,
            pool_env=("SEGMENTER_API_KEYS", "REWRITER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info("segmenter disabled: API key not configured, fallback to single segment (no LLM)")
            result = self._fallback_single(outline_scene, outline_chars, outline_mood)
            self._cache[cache_key] = result
            return result

        # 正文短/缺失/强制单段：调 LLM 做单段场景蒸馏（基于 outline）
        if force_single or not chapter_content or len(chapter_content) < MIN_CONTENT_LEN_FOR_SPLIT:
            result = await self._distill_single_with_llm(
                chapter_content, chapter_outline, outline_scene, outline_chars, outline_mood,
            )
            self._cache[cache_key] = result
            return result

        import time
        start = time.time()
        try:
            segments = await asyncio.wait_for(
                self._call_llm(chapter_content, chapter_outline),
                timeout=SEGMENTER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("segmenter timed out, fallback")
            self._stats.append(SegmenterStats(False, True, 1, time.time() - start, "timeout"))
            return self._fallback_single(outline_scene, outline_chars, outline_mood)
        except Exception as e:
            logger.warning("segmenter failed: %s", e)
            self._stats.append(SegmenterStats(False, True, 1, time.time() - start, str(e)))
            return self._fallback_single(outline_scene, outline_chars, outline_mood)

        if not segments:
            self._stats.append(SegmenterStats(False, True, 1, time.time() - start, "empty_result"))
            return self._fallback_single(outline_scene, outline_chars, outline_mood)

        limit = self._segment_limit(chapter_content)
        finalized = self._finalize_segments(segments, limit=limit)
        self._stats.append(SegmenterStats(True, False, len(finalized), time.time() - start))
        self._cache[cache_key] = finalized
        return finalized

    def _fallback_single(
        self,
        outline_scene: str,
        outline_chars: List[str],
        outline_mood: str,
        environment_hint_en: str = "",
    ) -> List[SceneSegment]:
        """降级到单段。保留可选的 environment_hint_en（如果有）。"""
        safe_loc = outline_scene or "story scene"
        return self._finalize_segments(
            [
                SceneSegment(
                    segment_id=1,
                    location=safe_loc,
                    location_en="",
                    start_marker="",
                    summary="",
                    characters_present=list(outline_chars)[:3],
                    mood=outline_mood or "day",
                    environment_hint_en=environment_hint_en,
                )
            ],
            limit=1,
        )

    async def _call_llm(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
    ) -> List[SceneSegment]:
        """调 LLM 切段。返回 SceneSegment 列表，失败抛异常。"""
        segment_limit = self._segment_limit(chapter_content)
        system_prompt = self._build_system_prompt(segment_limit)
        user_prompt = self._build_user_prompt(
            chapter_content,
            chapter_outline,
            segment_limit,
        )
        client = self._get_client()

        # 截断章节正文（避免 token 爆炸）
        truncated = chapter_content[:SEGMENTER_CONTENT_CHAR_LIMIT]
        if len(chapter_content) > SEGMENTER_CONTENT_CHAR_LIMIT:
            truncated += "\n...(后续省略)..."

        user_prompt = user_prompt.replace("{{CONTENT}}", truncated)

        response = await client.chat.completions.create(
            model=resolve_request_model(SEGMENTER_MODEL),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=SEGMENTER_MAX_TOKENS,
            timeout=SEGMENTER_TIMEOUT,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("segmenter returned non-JSON: %s", raw[:200])
            return []

        segs_raw = data.get("segments", [])
        if not isinstance(segs_raw, list):
            return []

        segments: List[SceneSegment] = []
        for i, seg in enumerate(segs_raw):
            if not isinstance(seg, dict):
                continue
            try:
                segments.append(
                    SceneSegment(
                        segment_id=i + 1,
                        location=str(seg.get("location", "")).strip(),
                        location_en=str(seg.get("location_en", "")).strip(),
                        start_marker=str(seg.get("start_marker", "")).strip(),
                        end_marker=str(seg.get("end_marker", "")).strip(),
                        summary=str(seg.get("summary", ""))[:100],
                        characters_present=[
                            str(c).strip() for c in seg.get("characters_present", []) if c
                        ][:4],
                        speakers=[
                            str(c).strip() for c in seg.get("speakers", []) if c
                        ][:4],
                        visible_actors=[
                            str(c).strip() for c in seg.get("visible_actors", []) if c
                        ][:4],
                        mood=str(seg.get("mood", "day")).strip() or "day",
                        time_of_day=str(seg.get("time_of_day", "unknown")).strip() or "unknown",
                        event_signature=str(seg.get("event_signature", "")).strip()[:160],
                        key_objects=[
                            str(item).strip()
                            for item in seg.get("key_objects", [])
                            if item
                        ][:6],
                        visual_anchor=str(seg.get("visual_anchor", "")).strip()[:160],
                        environment_hint_en=str(seg.get("environment_hint_en", "")).strip()[:300],
                    )
                )
            except (ValidationError, ValueError) as e:
                logger.warning("segment %d schema error: %s", i, e)
                continue

        # 过滤掉空 location
        segments = [s for s in segments if s.location]
        return self._finalize_segments(segments, limit=segment_limit)

    async def _distill_single_with_llm(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        outline_scene: str,
        outline_chars: List[str],
        outline_mood: str,
    ) -> List[SceneSegment]:
        """
        2026-06-15 — 正文短/缺失/强制单段时，调 LLM 基于 outline 做单段场景蒸馏。

        不切段，但让 LLM 读 outline 摘要（+ 可选正文片段），分析本章发生的具体场景，
        输出 environment_hint_en（英文环境视觉描述）。

        失败时降级到 _fallback_single（不调 LLM）。
        """
        import time
        start = time.time()
        try:
            seg = await asyncio.wait_for(
                self._call_llm_single(chapter_content, chapter_outline),
                timeout=SEGMENTER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("distill_single timed out, fallback")
            self._stats.append(SegmenterStats(False, True, 1, time.time() - start, "timeout"))
            return self._fallback_single(outline_scene, outline_chars, outline_mood)
        except Exception as e:
            logger.warning("distill_single failed: %s", e)
            self._stats.append(SegmenterStats(False, True, 1, time.time() - start, str(e)))
            return self._fallback_single(outline_scene, outline_chars, outline_mood)

        if not seg:
            self._stats.append(SegmenterStats(False, True, 1, time.time() - start, "empty_result"))
            return self._fallback_single(outline_scene, outline_chars, outline_mood)

        finalized = self._finalize_segments([seg], limit=1)
        self._stats.append(SegmenterStats(True, False, len(finalized), time.time() - start))
        return finalized

    async def _call_llm_single(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
    ) -> Optional[SceneSegment]:
        """
        单段场景蒸馏 LLM 调用。基于 outline（+ 可选正文片段）分析场景，
        输出 1 个 SceneSegment（含 environment_hint_en）。
        """
        system_prompt = self._build_single_distill_system_prompt()
        user_prompt = self._build_single_distill_user_prompt(chapter_content, chapter_outline)
        client = self._get_client()

        response = await client.chat.completions.create(
            model=resolve_request_model(SEGMENTER_MODEL),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=min(SEGMENTER_MAX_TOKENS, 800),
            timeout=SEGMENTER_TIMEOUT,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("distill_single returned non-JSON: %s", raw[:200])
            return None

        try:
            location = str(data.get("location", "")).strip() or chapter_outline.get("scene", "") or "scene"
            return SceneSegment(
                segment_id=1,
                location=location,
                location_en=str(data.get("location_en", "")).strip(),
                start_marker="",
                summary=str(data.get("summary", ""))[:100],
                characters_present=[
                    str(c).strip() for c in (chapter_outline.get("characters") or []) if c
                ][:4],
                mood=str(data.get("mood", "day")).strip() or chapter_outline.get("emotion") or "day",
                environment_hint_en=str(data.get("environment_hint_en", "")).strip()[:300],
            )
        except (ValidationError, ValueError) as e:
            logger.warning("distill_single schema error: %s", e)
            return None

    def _build_single_distill_system_prompt(self) -> str:
        return (
            "你是一位小说场景分析专家。你的任务：阅读本章大纲（和可选的正文片段），"
            "分析本章发生的具体物理场景，并产出该场景的纯环境英文视觉描述。\n\n"
            "输出 JSON 格式:\n"
            '{"location": "中文场景地点", "location_en": "english location", '
            '"summary": "<=50字场景摘要（仅环境/氛围，不含人物动作）", '
            '"mood": "day|night|dusk|dawn|rain|snow|fog", '
            '"environment_hint_en": "english-only environment visual description"}\n\n'
            "要求:\n"
            "1. location: 本章发生的物理场景地点（中文短词，如 '废弃厂房' / '南城公园'）。\n"
            "2. location_en: 简短英文，如 'abandoned factory' / 'city park'。\n"
            "3. mood: 必须从 day/night/dusk/dawn/rain/snow/fog/default 选一个。\n"
            "4. environment_hint_en（核心字段）: 该场景的纯环境视觉描述，英文，80-150 字符。\n"
            "   - 必须基于本章剧情推断本章发生的物理场景，描述该场景的：\n"
            "     地点类型、建筑、家具、关键物件、光线、天气、氛围。\n"
            "   - 例如：本章大纲是 '陆沉在深夜的废弃厂房中重生'，应输出\n"
            "     'interior of an abandoned industrial factory at night, rusted steel beams above "
            "cracked concrete floor, broken windows letting in faint moonlight, scattered debris and "
            "overturned machinery, deep shadow pockets, cold desolate atmosphere'\n"
            "   - 例如：本章大纲是 '朝堂议事'，应输出\n"
            "     'vast imperial court hall with towering vermilion columns, polished marble floor "
            "reflecting golden sunlight, ornate coffered ceiling painted with cloud motifs'\n"
            "   - 绝对禁止：任何人物、角色名、动作、表情、对白、姿势；\n"
            "     禁止 'a person / someone / a figure / stands / sits / looks / says / smiles / "
            "watches' 等任何人物或动作词。\n"
            "5. 这个 environment_hint_en 会直接喂给图像生成模型作背景图，"
            "任何人物语义都会污染画面，必须只描述环境本身。"
        )

    def _build_single_distill_user_prompt(
        self, chapter_content: str, chapter_outline: Dict[str, Any],
    ) -> str:
        outline_scene = chapter_outline.get("scene", "") or "(大纲未提供场景)"
        outline_summary = chapter_outline.get("summary", "") or "(大纲未提供摘要)"
        outline_chars = chapter_outline.get("characters", []) or []
        outline_conflict = chapter_outline.get("conflict", "") or ""
        # 正文片段：如果有，取前 1500 字（够推断场景细节，不至于 token 爆炸）
        content_snippet = ""
        if chapter_content:
            content_snippet = chapter_content[:SEGMENTER_SINGLE_CONTENT_CHAR_LIMIT]
            if len(chapter_content) > SEGMENTER_SINGLE_CONTENT_CHAR_LIMIT:
                content_snippet += "\n...(后续省略)..."

        parts = [
            "本章大纲信息:",
            f"- 大纲场景: {outline_scene}",
            f"- 大纲摘要: {outline_summary}",
            f"- 大纲冲突: {outline_conflict}",
            f"- 大纲出场人物: {outline_chars}",
        ]
        if content_snippet:
            parts.extend(["", "章节正文片段（前 1500 字，用于补充场景细节）:", content_snippet])
        else:
            parts.append("")
            parts.append("（章节正文尚未生成，仅基于大纲分析场景）")
        parts.append("")
        parts.append("请分析本章发生的具体物理场景，输出 JSON。")
        parts.append("environment_hint_en 必须是基于本章剧情推断出的纯环境英文视觉描述。")
        return "\n".join(parts)

    def _build_system_prompt(
        self,
        segment_limit: int = MAX_SEGMENTS_PER_CHAPTER,
    ) -> str:
        return (
            "你是一位视觉小说分镜分析专家。你的任务是把章节正文切成由内容驱动的"
            f"视觉时刻，最多 {segment_limit} 段。\n\n"
            "判定规则:\n"
            "1. 地点变化必须切段；同一地点离开后再次返回也必须建立新段。\n"
            "2. 同一地点内，时间/天气/光线、破坏状态、关键物件、镜头关注事件发生"
            "明显变化时切段。例如同一书房从白天谈话变为深夜失火，必须是两段。\n"
            "3. 命名人物首次进入画面、实际说话者组合变化，或出现可被看见的关键动作"
            "（拔刀、开门、坠落、爆炸、追逐等）时，可建立新段。\n"
            "4. 不因普通情绪、纯心理活动或每一句对白切段；相邻段应有可见差异。"
            f"通常每段约 {TARGET_CHARS_PER_VISUAL_MOMENT} 字。\n"
            "5. characters_present 只写本段在场的命名人物；speakers 只写实际发言者；"
            "visible_actors 只写做出可见动作的人。泛称路人、群众、士兵不要冒充命名人物。\n\n"
            "输出 JSON 格式:\n"
            '{"segments": [{"location": "中文地点", "location_en": "english location", '
            '"start_marker": "原文开头短语", "end_marker": "原文结尾短语", '
            '"summary": "<=50字视觉摘要", "characters_present": ["命名角色"], '
            '"speakers": ["实际说话者"], "visible_actors": ["实际动作主体"], '
            '"mood": "day|night|dusk|dawn|rain|snow|fog", '
            '"time_of_day": "day|dawn|dusk|night|interior|unknown", '
            '"event_signature": "本段可见事件短语", '
            '"key_objects": ["关键环境物件"], '
            '"visual_anchor": "地点+时间+事件+物件的短语", '
            '"environment_hint_en": "english-only environment visual description"}]}\n\n'
            "location_en 字段用于后续图像生成，必须是简短英文视觉描述，"
            "如 'imperial court hall' / 'palace garden' / 'city street at night'。\n"
            "mood 必须从 day/night/dusk/dawn/rain/snow/fog/default 选一个。\n\n"
            "environment_hint_en（重要！）: 该段的纯环境视觉描述，英文，80-150 字符。\n"
            "- 仅描述：地点、建筑、家具、物体、光线、天气、氛围。\n"
            "- 绝对禁止：任何人物、角色名、动作、表情、对白、姿势；\n"
            "  禁止出现 'a person / someone / a figure / a man / a woman / stands / sits / "
            "looks / says / smiles / watches' 等任何人物或动作词。\n"
            "- 若原文是 '林墨坐在桌前看芯片，苏晚晴在分析'，应转译成 "
            "'a dim workbench lit by blue chip glow, scattered electronic components on the table, "
            "window view of night city street, quiet indoor atmosphere'。\n"
            "- 这个字段会直接喂给图像生成模型作背景图，任何人物语义都会污染画面。"
        )

    def _build_user_prompt(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        segment_limit: int = MAX_SEGMENTS_PER_CHAPTER,
    ) -> str:
        outline_scene = chapter_outline.get("scene", "") or "(大纲未提供场景)"
        outline_summary = chapter_outline.get("summary", "") or "(大纲未提供摘要)"
        outline_chars = chapter_outline.get("characters", []) or []
        return (
            f"章节大纲:\n"
            f"- 大纲场景: {outline_scene}\n"
            f"- 大纲摘要: {outline_summary}\n"
            f"- 大纲出场人物: {outline_chars}\n\n"
            f"章节正文:\n{{{{CONTENT}}}}\n\n"
            f"请按正文切成最多 {segment_limit} 个视觉时刻。即使大纲场景为空，也要从"
            f"章节正文识别真实地点、时间、事件、关键物件和命名人物入场。\n"
            f"同一地点出现新的视觉事件时保留为不同段，不要按 location 去重。\n"
            f"对每个段必须输出 environment_hint_en（纯环境英文视觉描述，禁止任何人物/动作/对白）。\n"
            f"输出 JSON。"
        )

    def get_stats(self) -> Dict[str, Any]:
        """监控指标。"""
        if not self._stats:
            return {"total": 0, "enabled": SCENE_SEGMENTER_ENABLED}
        success = [s for s in self._stats if s.success]
        fallback = [s for s in self._stats if s.fallback]
        multi = [s for s in success if s.segment_count > 1]
        avg_dur = sum(s.duration_s for s in self._stats) / len(self._stats)
        return {
            "total": len(self._stats),
            "success": len(success),
            "fallback": len(fallback),
            "multi_scene_chapters": len(multi),
            "multi_scene_rate": round(len(multi) / max(1, len(success)), 3),
            "avg_duration_s": round(avg_dur, 3),
            "enabled": SCENE_SEGMENTER_ENABLED,
            "model": SEGMENTER_MODEL,
        }


# 全局实例
scene_segmenter_service = SceneSegmenterService()

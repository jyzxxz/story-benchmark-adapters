"""
Project-level visual style profile for generated image assets.

Project-visual-bible 契约（project-visual-bible-v4）后：本模块降级为兼容层。
``build_profile`` 内部委托 :mod:`project_visual_bible_service`，把不可变的项目级
``ProjectVisualBible`` 反向映射到旧 ``VisualStyleProfile`` 字段，保证三国路由
和其他旧调用方零感知。新代码应直接消费 ``ProjectVisualBible``。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, List, Optional

from app.schemas import StyleTag
from app.services.project_visual_bible_service import (
    ProjectVisualBible,
    project_visual_bible_service,
)


@dataclass(frozen=True)
class VisualStyleProfile:
    version: str
    style_family: str
    project_genre: str
    art_direction: str
    portrait_prompt_en: str
    background_prompt_zh: str
    keyframe_prompt_en: str
    palette: str
    lighting: str
    line_rendering: str
    mood: str
    negative_style_en: str
    fingerprint: str = ""
    source_work: str = ""
    source_profile_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualStyleProfileService:
    """Builds stable visual style and character identity anchors."""

    VERSION = "visual_style_profile_v2"

    def build_profile(
        self,
        story_bible: Any,
        project_style: str = "",
        project_title: str = "",
        source_work: str = "",
        source_context: str = "",
    ) -> VisualStyleProfile:
        """[DEPRECATED] 委托 :mod:`project_visual_bible_service`。

        保留是为了兼容现有调用方（三国路由、prompt_builder_service 等）。
        返回的 ``VisualStyleProfile`` 字段全部派生自锁定的 ``ProjectVisualBible``，
        因此 portrait / background / keyframe 三个 prompt 字段共享同一套画风块。
        """
        bible = project_visual_bible_service.build_for_project(
            story_bible,
            project_title=project_title,
            project_style=project_style,
            source_work=source_work,
            source_context=source_context,
        )
        # 旧 genre 字段必须读取与 bible 同时锁定的 setting classification。
        # 重新推断会让后来补充的角色视觉元数据改变 genre，造成三类素材分裂。
        classification = project_visual_bible_service.load_locked_classification(
            story_bible
        )
        if classification is None:
            classification = project_visual_bible_service.infer_classification(
                story_bible,
                project_title,
                project_style,
                source_work=source_work,
                source_context=source_context,
            )
        legacy_genre = _classification_to_legacy_genre(classification)
        return self.profile_from_bible(bible, legacy_genre)

    def profile_from_bible(
        self, bible: ProjectVisualBible, legacy_genre: str = "",
    ) -> VisualStyleProfile:
        """把锁定的 bible 反向映射到旧 VisualStyleProfile 字段。

        关键不变量：``portrait_prompt_en`` / ``background_prompt_zh`` /
        ``keyframe_prompt_en`` 都派生自 bible 的同一套 medium/linework/shading/
        texture/palette —— 不再是三套独立画风块。
        """
        shared_block = (
            f"{bible.art_direction}. Line: {bible.linework}. "
            f"Shading: {bible.shading}. Texture: {bible.texture}. "
            f"Palette: {', '.join(bible.base_palette)} ({bible.palette_policy})."
        )
        mood = self._infer_mood_from_palette(bible)
        profile = VisualStyleProfile(
            version=self.VERSION,
            style_family=bible.style_family,
            project_genre=legacy_genre or _medium_to_legacy_genre(bible),
            art_direction=bible.art_direction,
            # 旧契约下三个分离的画风块现在共享同一份 shared_block
            # Framing is decided per asset (half/full body). Keeping it out of
            # the project-wide style block prevents contradictory prompts.
            # Portrait geometry/alpha rules are supplied as structured fields
            # to the LLM rewriter. This project-wide value is deliberately
            # style-only so it can never contradict the requested shot.
            portrait_prompt_en=f"{shared_block}.",
            background_prompt_zh=(
                f"视觉小说背景图，{bible.art_direction}。线稿：{bible.linework}。"
                f"材质：{bible.texture}。色板：{'、'.join(bible.base_palette)}。"
                f"{bible.background_role}"
            ),
            keyframe_prompt_en=f"{shared_block}. {bible.keyframe_role}",
            palette=", ".join(bible.base_palette),
            lighting=bible.lighting_policy,
            line_rendering=bible.linework,
            mood=mood,
            negative_style_en="; ".join(bible.forbidden_styles),
            source_work=bible.source_work,
            source_profile_id=bible.source_profile_id,
        )
        # One project has one authoritative fingerprint.  Re-hashing this
        # compatibility projection created a second value and let portrait,
        # background and keyframe caches disagree about the active style.
        return replace(profile, fingerprint=bible.fingerprint)

    # Kept for callers written against the v1 compatibility service.
    def _profile_from_bible(
        self, bible: ProjectVisualBible, legacy_genre: str = "",
    ) -> VisualStyleProfile:
        return self.profile_from_bible(bible, legacy_genre)

    def infer_project_genre(
        self,
        story_bible: Dict[str, Any],
        project_style: str = "",
        project_title: str = "",
    ) -> str:
        """[DEPRECATED] 委托 project_visual_bible_service.infer_classification。

        新分类把 genre 和 medium 解耦；这里为了兼容旧调用方仍返回一个 genre 字符串。
        """
        sb_dict = story_bible if isinstance(story_bible, dict) else {}
        classification = project_visual_bible_service.load_locked_classification(
            story_bible
        ) or project_visual_bible_service.infer_classification(
            sb_dict, project_title, project_style,
        )
        return _classification_to_legacy_genre(classification)

    def character_visual_anchor(
        self,
        character_name: str,
        appearance: str,
        gender: str = "",
    ) -> str:
        parts = []
        if character_name:
            parts.append(f"identity anchor for {character_name}")
        if gender:
            parts.append(f"gender: {gender}")
        if appearance:
            parts.append(f"fixed appearance: {appearance}")
        parts.append(
            "same face, same hairstyle, same body type, same signature outfit silhouette, "
            "expression changes only"
        )
        return "; ".join(parts)

    def background_style_tags(
        self,
        profile: VisualStyleProfile,
        spec: Optional[Any] = None,
    ) -> List[StyleTag]:
        """[DEPRECATED] 旧背景标签 API。

        新链路应直接用 :class:`SceneVisualTreatment`（背景风格分类器的输出）。
        这里保留是为了让 asset_management_service 的旧分支在还没接入新链路时
        不崩。返回的标签都派生自 profile（即 bible 共享块），不再含独立画风。

        Note: ``art_style`` 必须是短词（StyleTag.value 上限 80 字符），不能用
        整段 ``background_prompt_zh`` —— 那是 prompt 串，不是标签。
        """
        camera = "35mm环境建立镜头"
        if spec is not None and getattr(spec, "camera_shot_type", "") == "establishing_wide":
            camera = "24mm广角环境建立镜头"
        def short_tag(value: str) -> str:
            text = str(value or "").strip()
            return text if len(text) <= 80 else text[:77] + "..."

        # 派生短描述词（与 style_family 对齐，旧标签语义）
        art_style_short = (
            f"{profile.style_family} / {profile.art_direction[:40]}"
            if profile.art_direction else profile.style_family
        )
        return [
            StyleTag(dimension="art_style", value=short_tag(art_style_short)),
            StyleTag(dimension="color_palette", value=short_tag(profile.palette)),
            StyleTag(dimension="lens_or_camera_feel", value=short_tag(camera)),
            StyleTag(dimension="texture_or_rendering", value=short_tag(profile.line_rendering)),
            StyleTag(dimension="mood", value=short_tag(profile.mood)),
        ]

    def _infer_mood_from_palette(self, bible: ProjectVisualBible) -> str:
        """从 bible 派生一个 mood 字符串（旧字段兼容）。"""
        # 旧调用方只用 mood 做日志/展示，不做风格决策，所以这里给一个稳定派生值
        if "warm" in bible.palette_policy or "warm" in str(bible.base_palette).lower():
            return "温暖统一氛围"
        if "cool" in bible.palette_policy or "neon" in str(bible.base_palette).lower():
            return "冷峻统一氛围"
        return "统一视觉小说氛围"

    def _fingerprint(self, profile: VisualStyleProfile) -> str:
        payload = profile.to_dict()
        payload["fingerprint"] = ""
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------
# Legacy genre mapping helpers
# ------------------------------------------------------------------


def _medium_to_legacy_genre(bible: ProjectVisualBible) -> str:
    """Map a locked bible back to the legacy 5-bucket genre string.

    Used only for backwards compatibility with old callers that read
    ``profile.project_genre``. New code should consume ``bible.style_family``
    + the classification in ``raw_json["project_visual_bible"]["classification"]``.
    """
    if bible.style_family == "ink_color_guofeng":
        return "historical"
    if bible.style_family == "concept_art_realism":
        return "realistic"
    if bible.style_family == "digital_painterly":
        return "painterly"
    return "anime"  # anime_cel falls through here


def _classification_to_legacy_genre(classification) -> str:
    """Map a ProjectVisualClassification to the legacy genre string."""
    genre_map = {
        "modern_campus": "modern",
        "sci-fi": "sci-fi",
        "fantasy": "fantasy",
        "historical_guofeng": "historical",
        "wuxia": "historical",
        "horror": "modern",
    }
    return genre_map.get(classification.narrative_genre, "modern")


visual_style_profile_service = VisualStyleProfileService()

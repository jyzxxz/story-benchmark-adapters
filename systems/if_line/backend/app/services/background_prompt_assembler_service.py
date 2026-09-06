"""
Background Prompt Assembler Service (Stage_Background_AR L3.17-L3.18)

输入：BackgroundSceneSpec + style_tags + forbidden_characters
输出：final prompt string（deterministic 9 段拼接）

职责：
- 严格按固定顺序拼接 prompt（不自由重写）
- 始终以 ban clause 结尾
- 不引入新人物/动作/对白

设计哲学：Docs/researches/Stage_Background_AR/00_philosophy.md
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas import BackgroundSceneSpec, StyleTag
from app.services.background_positive_text_sanitizer import (
    forbidden_story_names,
    sanitize_positive_background_text,
)
from app.services.background_story_entity_text import (
    BACKGROUND_ENTITY_EXCLUSION_VERSION,
    STORY_ENTITY_EXCLUSION_HEADER_CN,
    STORY_ENTITY_EXCLUSION_HEADER_EN,
    format_story_entity_exclusion,
)

logger = logging.getLogger("bg_prompt_assembler")


class BackgroundPromptAssembler:
    """L3.17 — 确定性 9 段拼接器"""

    # 9 段顺序
    SECTION_ORDER = [
        "scene_name",
        "environment",
        "architecture_props",
        "lighting_weather_time",
        "camera",
        "composition",
        "people_policy",
        "style_tags",
        "ban_clause",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._config = config or self._load_default_config()

    @staticmethod
    def _load_default_config() -> Dict[str, Any]:
        cfg_path = Path(__file__).parent.parent / "config" / "image_generation_profiles.json"
        if not cfg_path.exists():
            return {}
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("load config failed: %s", e)
            return {}

    @property
    def _negative_clauses(self) -> Dict[str, str]:
        return self._config.get("background_negative_clauses", {})

    @property
    def _camera_shots(self) -> Dict[str, Dict[str, Any]]:
        return self._config.get("background_camera_shots", {}).get("shots", {})

    # L3.18: 9 个 _section_* 私有方法

    def _section_scene_name(self, spec: BackgroundSceneSpec) -> str:
        scene_name = self._sanitize_positive(spec.scene_name, spec) or "空环境场景"
        return f"场景名：{scene_name}"

    def _section_environment(self, spec: BackgroundSceneSpec) -> str:
        return self._sanitize_positive(spec.environment_description, spec) or (
            "空旷场地保留原有地貌、建筑材质与静态陈设，画面仅表现地点本身。"
        )

    def _section_architecture_props(self, spec: BackgroundSceneSpec) -> str:
        parts: List[str] = []
        if spec.architecture:
            architecture = self._sanitize_positive(spec.architecture, spec)
            if architecture:
                parts.append(f"建筑：{architecture}")
        if spec.props:
            props = self._sanitize_positive(spec.props, spec)
            if props:
                parts.append(f"道具：{props}")
        return "\n".join(parts)

    def _section_lighting_weather_time(self, spec: BackgroundSceneSpec) -> str:
        parts: List[str] = []
        lighting = self._sanitize_positive(spec.lighting, spec)
        if lighting:
            parts.append(f"光线：{lighting}")
        if spec.weather:
            weather = self._sanitize_positive(spec.weather, spec)
            if weather:
                parts.append(f"天气：{weather}")
        parts.append(f"时间：{self._time_of_day_label(spec.time_of_day)}")
        atmosphere = self._sanitize_positive(spec.atmosphere, spec)
        if atmosphere:
            parts.append(f"氛围：{atmosphere}")
        return "\n".join(parts)

    def _section_camera(self, spec: BackgroundSceneSpec) -> str:
        shot = self._camera_shots.get(spec.camera_shot_type, {})
        name = shot.get("name_cn") or self._camera_type_label(spec.camera_shot_type)
        phrase = shot.get("prompt_phrase", "广角环境建立镜头")
        return f"镜头：{name} — {phrase}"

    def _section_composition(self, spec: BackgroundSceneSpec) -> str:
        if spec.composition_constraints:
            composition = self._sanitize_positive(spec.composition_constraints, spec)
            if composition:
                return f"构图约束：{composition}"
        return ""

    @staticmethod
    def _sanitize_positive(text: str | None, spec: BackgroundSceneSpec) -> str:
        return sanitize_positive_background_text(
            text,
            forbidden_names=forbidden_story_names(
                spec.forbidden_characters,
                spec.forbidden_entities,
            ),
        )

    def _section_people_policy(self, spec: BackgroundSceneSpec) -> str:
        mode = spec.people_policy.mode
        rationale = self._localize_text(
            self._sanitize_positive(spec.people_policy.rationale, spec) or "纯环境背景"
        )
        return f"人物策略：{self._people_policy_label(mode)}（{rationale}）"

    def _section_style_tags(self, tags: List[StyleTag]) -> str:
        if not tags:
            return ""
        # 按 dimension 顺序排
        ordered = sorted(tags, key=lambda t: ["art_style", "color_palette", "lens_or_camera_feel", "texture_or_rendering", "mood"].index(t.dimension))
        rendered = [
            f"{self._style_dimension_label(t.dimension)}={self._localize_text(t.value)}"
            for t in ordered
        ]
        return "风格标签：" + "，".join(rendered)

    def _section_ban_clause(self, spec: BackgroundSceneSpec) -> str:
        """组合 base + mode-specific + escalation_level_0 ban clause.

        Stage_Background_Entity_Exclusion — 禁入对象已从 ``no humans`` 升级为
        ``no story characters/entities``。当 ``spec.forbidden_entities`` 非空时，
        走结构化实体禁入；否则回退到 legacy ``forbidden_characters`` 字符串禁令。
        """
        clauses = self._negative_clauses
        mode = spec.people_policy.mode
        parts: List[str] = [clauses.get("base", "")]
        if mode == "empty_required":
            parts.append(clauses.get("empty_required", ""))
        elif mode == "background_people_optional":
            parts.append(clauses.get("background_people_optional", ""))
        elif mode == "background_groups_required":
            parts.append(clauses.get("background_groups_required", ""))
        # enforcing
        parts.append(clauses.get("establishing_shot_enforcement", ""))
        # Stage_Background_Entity_Exclusion — version stamp always present
        # so that bumping BACKGROUND_ENTITY_EXCLUSION_VERSION invalidates
        # every cached background image even when the entity list is empty.
        parts.append(f"[entity-exclusion-version: {BACKGROUND_ENTITY_EXCLUSION_VERSION}]")
        # Story-entity exclusion block — always present so the rule applies
        # regardless of whether the cast list is empty.
        parts.append(STORY_ENTITY_EXCLUSION_HEADER_CN)
        parts.append(STORY_ENTITY_EXCLUSION_HEADER_EN)
        if spec.forbidden_entities:
            parts.append(format_story_entity_exclusion(spec.forbidden_entities))
        elif spec.forbidden_characters:
            parts.append(self._format_named_cast_exclusion(spec.forbidden_characters))
        return "\n\n".join(p for p in parts if p).strip()

    @staticmethod
    def _format_named_cast_exclusion(chars: List[str]) -> str:
        """格式化命名角色禁令（中英分段）"""
        zh = [c for c in chars if re.search(r"[一-鿿]", c)]
        en = [c for c in chars if c and not re.search(r"[一-鿿]", c)]
        parts = ["禁用命名角色（永远禁止出现在背景图中）："]
        if zh:
            parts.append(f"- 中文：{', '.join(zh)}")
        if en:
            parts.append(f"- 英文/别名：{', '.join(en)}")
        parts.append("画面中不得出现、提及或暗示以上任何角色。")
        return "\n".join(parts)

    @staticmethod
    def _camera_type_label(camera_shot_type: str) -> str:
        labels = {
            "establishing_wide": "环境建立镜头（广角远景）",
            "environment_medium": "环境广角中景",
            "high_angle_distant": "高机位远景",
            "interior_wide": "室内一体化广角",
            "telephoto_compressed": "长焦压缩远景",
            "low_angle_perspective": "低机位纵深",
        }
        return labels.get(camera_shot_type, "广角环境镜头")

    @staticmethod
    def _people_policy_label(mode: str) -> str:
        labels = {
            "empty_required": "必须空场",
            "background_people_optional": "公共空间仍按空场处理",
            "background_groups_required": "公共活动场景仍按空场处理",
        }
        return labels.get(mode, mode)

    @staticmethod
    def _time_of_day_label(value: str) -> str:
        labels = {
            "dawn": "黎明",
            "morning": "早晨",
            "noon": "正午",
            "afternoon": "下午",
            "dusk": "黄昏",
            "evening": "傍晚",
            "night": "夜晚",
            "late_night": "深夜",
            "unknown": "未指定",
        }
        return labels.get(value, value)

    @staticmethod
    def _style_dimension_label(dimension: str) -> str:
        labels = {
            "art_style": "画风",
            "color_palette": "色彩",
            "lens_or_camera_feel": "镜头感",
            "texture_or_rendering": "质感",
            "mood": "情绪",
        }
        return labels.get(dimension, dimension)

    @staticmethod
    def _localize_text(text: str) -> str:
        if not text:
            return text
        replacements = {
            "fallback safe empty environment: analyzer/segmenter unavailable": "安全兜底空场：分析器或分段器不可用",
            "forced empty_required by global background policy": "已按全局背景策略强制设为空场",
            "empty environment": "空环境",
            "24mm广角 establishing shot": "24mm广角环境建立镜头",
            "超广角 establishing shot": "超广角环境建立镜头",
            "establishing shot": "环境建立镜头",
            "wide establishing shot": "广角环境建立镜头",
            "anime": "动画风",
        }
        out = str(text)
        for src, dst in replacements.items():
            out = out.replace(src, dst)
        out = out.replace("; ", "；").replace(";", "；")
        return out

    # --------- main entry ---------

    def assemble(
        self,
        spec: BackgroundSceneSpec,
        style_tags: Optional[List[StyleTag]] = None,
    ) -> str:
        """
        9 段 deterministic 顺序拼接 → final prompt。

        Args:
            spec: BackgroundSceneSpec
            style_tags: 风格标签（若 None 则用 spec.style_tags）

        Returns:
            final prompt string（始终以 ban clause 结尾）
        """
        tags = style_tags if style_tags is not None else spec.style_tags
        sections: List[str] = [
            self._section_scene_name(spec),
            self._section_environment(spec),
            self._section_architecture_props(spec),
            self._section_lighting_weather_time(spec),
            self._section_camera(spec),
            self._section_composition(spec),
            self._section_people_policy(spec),
            self._section_style_tags(tags),
            self._section_ban_clause(spec),
        ]
        # 过滤空段，保留顺序
        return "\n\n".join(s for s in sections if s and s.strip()).strip()

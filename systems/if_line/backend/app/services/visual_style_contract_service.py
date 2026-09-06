"""版本化视觉风格契约，作为立绘和背景提示词的统一风格来源。

提示词构建只消费 :class:`VisualStyleContract`。立绘和背景共用同一个
``shared_art_direction``，仅在透明角色立绘、空场景背景等媒介职责上拆分。
``forbidden`` 同时约束两类素材。

修改 ``version`` 会让下游提示词哈希变化，从而触发重新生成。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class VisualStyleContract:
    """Versioned art-direction contract shared by portrait + background prompts."""

    version: str
    identity: str
    medium: str
    linework: str
    shading: str
    palette: Tuple[str, ...]
    lighting: str
    texture: str
    perspective: str
    portrait_rules: Tuple[str, ...]
    background_rules: Tuple[str, ...]
    forbidden: Tuple[str, ...]

    @property
    def shared_art_direction(self) -> str:
        """Block injected verbatim into BOTH portrait and background prompts.

        The block is intentionally vocabulary-only — no medium-specific cues
        like "transparent background" or "no humans", which belong to the
        portrait/background role layers.
        """
        palette_text = ", ".join(self.palette)
        return (
            f"STYLE SIGNATURE: {self.version}. "
            f"SHARED ART DIRECTION: {self.medium}. "
            f"Line work: {self.linework}. "
            f"Shading: {self.shading}. "
            f"Palette: {palette_text}, overall low saturation. "
            f"Lighting: {self.lighting}. "
            f"Texture: {self.texture}. "
            f"Perspective: {self.perspective}. "
            f"The character sprites and the environment backgrounds belong to "
            f"the same visual novel art bible — identical palette, line "
            f"character, paper texture and lighting language."
        )

    @property
    def portrait_role(self) -> str:
        return (
            "PORTRAIT ROLE: transparent full-body character sprite, RGBA PNG, "
            "alpha channel background fully transparent, crisp silhouette, "
            + ", ".join(self.portrait_rules) + "."
        )

    @property
    def background_role(self) -> str:
        return (
            "BACKGROUND ROLE: empty environment establishing shot, no humans, "
            "no characters, no animals, no text, no watermark, slightly lower "
            "contrast than the character sprites, lower foreground visually "
            "readable for compositing full-body sprites. "
            + ", ".join(self.background_rules) + "."
        )

    @property
    def forbidden_clause(self) -> str:
        if not self.forbidden:
            return ""
        return "FORBIDDEN STYLE: " + ", ".join(self.forbidden) + "."


# ============================================================
# Canonical Three Kingdoms contract (ink-and-color, v2)
# ============================================================

THREE_KINGDOMS_INK_V2 = VisualStyleContract(
    version="three-kingdoms-ink-v2",
    identity="three-kingdoms",
    medium=(
        "late Han dynasty and Three Kingdoms 2D hand-painted visual novel "
        "illustration, Chinese ink-and-color historical art bible"
    ),
    linework=(
        "restrained confident linework with subtle brush-like lift and fall, "
        "neither thick oil strokes nor hard Japanese cel-shade edges"
    ),
    shading=(
        "ink wash tonal modulation combined with light flat painting, "
        "character silhouettes slightly crisper than the environment, both "
        "sharing the same color system and paper texture"
    ),
    palette=(
        "muted earth red",
        "aged bronze",
        "dark jade",
        "warm ivory",
        "ink black",
    ),
    lighting=(
        "restrained cinematic environmental light, warm-cool balance keeps "
        "character light direction and color temperature composable into "
        "the scene"
    ),
    texture=(
        "very subtle paper grain across both characters and backgrounds, "
        "characters are NOT glossy-smooth while the background has paper grain"
    ),
    perspective=(
        "shallow cinematic perspective, character sprites sized for "
        "foreground compositing against the painted environment"
    ),
    portrait_rules=(
        "full body visible from head to feet",
        "head without helmet or hat never touches the top edge",
        "feet never clipped",
        "weapon and accessories fully preserved",
    ),
    background_rules=(
        "16:9 widescreen establishing view",
        "lower third left visually open for the speaking character sprite",
        "muted desaturated palette matching the character sprites",
    ),
    forbidden=(
        "photorealistic rendering",
        "3D game render",
        "modern clothing",
        "Japanese samurai clothing",
        "generic Japanese galgame style",
        "modern anime school style",
        "chibi proportions",
        "glossy plastic skin",
        "neon cyberpunk palette",
        "oil painting thick strokes",
    ),
)


def contract_for_three_kingdoms() -> VisualStyleContract:
    """Public accessor — bump this when the contract version changes."""
    return THREE_KINGDOMS_INK_V2


def default_contract() -> VisualStyleContract:
    """Default contract when no style lock is supplied (kept behavior-stable).

    Modern call sites pass an explicit contract; this exists so legacy paths
    that still call ``generate_portrait`` / ``generate_background`` without a
    style lock remain deterministic and don't reintroduce the old galgame /
    photorealistic defaults.
    """
    return THREE_KINGDOMS_INK_V2


__all__ = [
    "VisualStyleContract",
    "THREE_KINGDOMS_INK_V2",
    "contract_for_three_kingdoms",
    "default_contract",
]

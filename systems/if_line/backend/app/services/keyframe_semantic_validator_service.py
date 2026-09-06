"""Keyframe semantic validator — 关键帧画面与段落剧情的一致性验收。

背景图有 BackgroundStoryEntityValidatorService（实体泄漏验收），关键帧
此前没有任何出图后校验：段落写「镜像世界剧院」、画面画成「黄昏街道」
照样入库（kf20 类错配）。本服务补上这道闸门——用 VLM 对照段落文本检查
生成画面是否呈现了文本强调的核心视觉要素，输出缺失要素供重试 prompt
使用。

设计要点
--------
- 只消费已生成图片 + 段落文本，不调用生图 API。
- 判定标准宽松：允许构图/细节自由发挥，只有地点、主体或场景类型与文本
  明显不符、或文本明确强调的关键视觉对象缺失时才判不通过，避免误杀。
- 与背景验收器同构：``vlm_client`` 可注入，单测不需要真实 API key。
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kf_semantic_validator")

# 关键帧语义验收开关与重试上限（不含首次）
KEYFRAME_SEMANTIC_VALIDATE_ENABLED = (
    os.getenv("KEYFRAME_SEMANTIC_VALIDATE_ENABLED", "true").lower() == "true"
)
KEYFRAME_SEMANTIC_MAX_RETRIES = int(
    os.getenv("KEYFRAME_SEMANTIC_MAX_RETRIES", "2")
)


@dataclass(frozen=True)
class KeyframeSemanticValidation:
    """单张关键帧的验收结论。

    ``passed`` 是调用方关心的唯一比特；``missing_elements`` 供重试 prompt
    把缺失要素显式写进【重试指令】；``reasons`` 走日志与隔离元数据。
    """

    passed: bool
    missing_elements: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    raw_vlm_response: Optional[dict[str, Any]] = None


_SYSTEM_PROMPT = (
    "你是视觉小说关键帧语义验收器。给你一段剧情文本和据其生成的关键帧插画，"
    "判断画面是否忠实呈现了文本的核心视觉要素（地点、主体、关键动作或场景类型）。\n"
    "判定标准：\n"
    "- 构图、镜头、光线、次要细节允许自由发挥；\n"
    "- 画面呈现的地点/场景类型与文本明显不符 → 不通过；\n"
    "- 文本明确强调的关键视觉对象（特定道具、现象、环境特征）在画面中完全缺失"
    "且无法用其他要素替代 → 不通过；\n"
    "- 出场角色的姿态/情绪与文本有偏差但场景正确 → 通过。\n"
    "输出严格 JSON，不要 markdown 围栏。"
)


def _build_user_prompt(
    action_text: str,
    character_names: list[str],
    scene_hint: str,
) -> str:
    lines = [f"【剧情文本】\n{action_text}"]
    if character_names:
        lines.append("【出场角色】" + "、".join(character_names))
    if scene_hint:
        lines.append(f"【场景参考】{scene_hint}")
    lines.append(
        "请对照判断画面与剧情文本是否相符。输出 JSON:\n"
        "{\n"
        '  "passes": true/false,\n'
        '  "missing_elements": ["文本要求但画面缺失的关键视觉要素"],\n'
        '  "mismatch_reason": "若不通过，一句话说明画面实际呈现了什么",\n'
        '  "confidence": 0.0\n'
        "}"
    )
    return "\n".join(lines)


class KeyframeSemanticValidatorService:
    """VLM 关键帧语义验收器（镜像 BackgroundStoryEntityValidatorService）。"""

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        vlm_client: Any = None,
    ) -> None:
        self.config = dict(config or {})
        self._vlm_client_override = vlm_client

    def _get_client(self) -> Any:
        if self._vlm_client_override is not None:
            return self._vlm_client_override
        from app.services.image_generation_service import (
            BG_VISION_API_KEY,
            BG_VISION_BASE_URL,
        )
        from app.services.background_image_validator_service import (
            BackgroundImageValidatorService,
        )
        return BackgroundImageValidatorService()._get_async_vision_client(
            BG_VISION_API_KEY,
            BG_VISION_BASE_URL,
        )

    async def validate(
        self,
        image_path: Path,
        *,
        action_text: str,
        character_names: Optional[list[str]] = None,
        scene_hint: str = "",
        timeout: float = 12.0,
    ) -> KeyframeSemanticValidation:
        """校验一张关键帧是否呈现了段落剧情。

        空剧情文本 → 自动通过（无据可依）；图片缺失 → 不通过（调用方
        决定重试或隔离）。
        """
        action_text = (action_text or "").strip()
        if not action_text:
            return KeyframeSemanticValidation(
                passed=True,
                reasons=("no action text registered",),
            )
        if not image_path or not Path(image_path).exists():
            return KeyframeSemanticValidation(
                passed=False,
                reasons=(f"image not found: {image_path}",),
            )

        try:
            with open(image_path, "rb") as handle:
                img_bytes = handle.read()
        except OSError as exc:
            return KeyframeSemanticValidation(
                passed=False,
                reasons=(f"image read failed: {exc}",),
            )
        b64 = base64.b64encode(img_bytes).decode("ascii")
        suffix = Path(image_path).suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        data_url = f"data:{mime};base64,{b64}"

        import asyncio
        import json

        from app.services.image_generation_service import BG_VISION_MODEL

        user_prompt = _build_user_prompt(
            action_text,
            [str(name).strip() for name in (character_names or []) if str(name).strip()],
            scene_hint,
        )
        try:
            client = self._get_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=BG_VISION_MODEL,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_prompt},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        },
                    ],
                    temperature=0.0,
                    max_tokens=512,
                ),
                timeout=timeout,
            )
        except Exception as exc:  # 网络/模型异常不拦截发布链路
            logger.warning("keyframe semantic VLM call failed: %s", exc)
            return KeyframeSemanticValidation(
                passed=True,
                reasons=(f"vlm unavailable, accepted by default: {exc}",),
            )

        raw_text = ""
        try:
            raw_text = str(
                getattr(getattr(resp.choices[0], "message", None), "content", "") or ""
            )
            data = json.loads(raw_text)
        except Exception:
            logger.warning("keyframe semantic VLM response unparsable: %s", raw_text[:200])
            return KeyframeSemanticValidation(
                passed=True,
                reasons=("vlm response unparsable, accepted by default",),
                raw_vlm_response={"raw": raw_text[:500]},
            )

        missing = [
            str(item).strip()
            for item in (data.get("missing_elements") or [])
            if str(item).strip()
        ]
        reasons: list[str] = []
        mismatch = str(data.get("mismatch_reason") or "").strip()
        if mismatch:
            reasons.append(mismatch)
        if missing:
            reasons.append("缺失要素: " + "；".join(missing[:5]))
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return KeyframeSemanticValidation(
            passed=bool(data.get("passes") is True),
            missing_elements=tuple(missing),
            reasons=tuple(reasons) or ("semantic mismatch",),
            confidence=confidence,
            raw_vlm_response=data if isinstance(data, dict) else None,
        )

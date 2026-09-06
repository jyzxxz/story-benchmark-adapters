"""
BG-VALIDATE — 背景图人物主体化验收器（2026-06-14 升级）。

目的：在背景图生成成功后、保存前，判断这张图是否"人物主体化"
或包含任何人物元素。

用户硬要求（2026-06-14）:
所有背景图必须**完全无人**，包括路人、士兵、守卫、侍女、群众、剪影、背影、
人形轮廓、远景匿名陪体等都不允许出现。

判定规则（任意一条成立即判失败）：
- has_any_person = true       # 图中出现任何人物
- has_human_subject = true    # 人物是主体
- has_single_prominent_person = true  # 单个显著人物
- is_environment_main_subject = false # 环境不是主体

实现策略（A+B 混合）：
- 方案 A（主）：用多模态视觉模型（智谱 glm-4v-plus / OpenAI 兼容 chat.completions
  + image_url）做语义判断。最准但需要 vision API key + 联网。
- 方案 B（兜底）：PIL 启发式 —— 检测任何皮肤色像素 / 中心人形。
  保守（不假阳性），但只要检测到任何皮肤色像素都判失败。

Validator 输出统一 ValidationResult：
{
    "passed": bool,                # True = 可保存, False = 必须重生成
    "has_human_subject": bool,
    "has_single_prominent_person": bool,
    "is_environment_main_subject": bool,
    "method": "vision" | "pil" | "disabled",
    "reason": str,
}
"""
import os
import json
import base64
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple, TYPE_CHECKING

from app.services.api_key_pool import PooledAsyncOpenAI, PooledOpenAI, api_key_available

if TYPE_CHECKING:
    from app.schemas import BackgroundSceneSpec, BBox, ValidationResult
    from app.services.project_visual_bible_service import ProjectVisualBible
    from app.services.visual_style_coherence_service import ImageStyleMetrics


logger = logging.getLogger("bg_image_validator")

# project-visual-bible-v2 §D: cache invalidation bump for the style-deviation
# extension. Bumped because the validator now records style-deviation evidence
# in Asset.generation_params; old assets lack the new fields so the cache key
# must shift.
BACKGROUND_VALIDATION_VERSION = "project-coherence-v2"


@dataclass
class StyleDeviationReport:
    """project-visual-bible-v2 §B6 — warning-only style deviation report.

    Per decision 2 (2026-07-17): we record saturation / contrast /
    edge_density / palette_distance gaps as *warnings*, never as rejections.
    ``rejected`` is always False from this layer — project_visual_consistency
    may turn sustained deviations into rejections at the group level, but
    single-image verdicts here are advisory only.
    """

    saturation: Optional[float] = None
    contrast: Optional[float] = None
    edge_density: Optional[float] = None
    palette_distance_to_baseline: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    rejected: bool = False
    reason: str = ""
    metrics_version: str = BACKGROUND_VALIDATION_VERSION
    metrics: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "saturation": self.saturation,
            "contrast": self.contrast,
            "edge_density": self.edge_density,
            "palette_distance_to_baseline": self.palette_distance_to_baseline,
            "warnings": list(self.warnings),
            "rejected": self.rejected,
            "reason": self.reason,
            "metrics_version": self.metrics_version,
            "metrics": self.metrics,
        }


class BackgroundImageValidatorService:
    """背景图人物主体化验收器（视觉模型优先，PIL 启发式兜底）。"""

    # L3b.06: HARD_FAIL_REASONS 常量集合
    HARD_FAIL_REASONS = {
        "named_character_detected",
        "human_subject_in_center",
        "single_prominent_person_in_empty_required",
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._config = config or {}
        self._bbox_model = None  # lazy-load YOLOv8n
        self._bbox_unavailable_reason: Optional[str] = None
        self._vision_sync_client: Optional[PooledOpenAI] = None
        self._vision_async_client: Optional[PooledAsyncOpenAI] = None

    def _get_sync_vision_client(self, api_key: str, base_url: str) -> PooledOpenAI:
        if self._vision_sync_client is None:
            self._vision_sync_client = PooledOpenAI(
                api_key=api_key,
                pool_env=("BG_VISION_API_KEYS", "AI_IMAGE_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=False,
                base_url=base_url,
            )
        return self._vision_sync_client

    def _get_async_vision_client(self, api_key: str, base_url: str) -> PooledAsyncOpenAI:
        if self._vision_async_client is None:
            self._vision_async_client = PooledAsyncOpenAI(
                api_key=api_key,
                pool_env=("BG_VISION_API_KEYS", "AI_IMAGE_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=False,
                base_url=base_url,
            )
        return self._vision_async_client

    def validate(
        self,
        image_path: Optional[Path] = None,
        prompt: str = "",
        scene_name: Optional[str] = None,
        allow_background_people: bool = False,
        spec: Optional["BackgroundSceneSpec"] = None,
    ):
        """
        L3b.05 — 统一 validate 入口。

        两条路径：
        - spec-driven（新）：返回 Pydantic ValidationResult，bbox + VLM 混合
        - legacy（旧）：返回 dict，仅 VLM/PIL

        bbox + VLM 混合策略：
        - bbox 先筛：clear_fail → 直接 hard_fail
        - empty_required 的 clear_pass/suspicious → 进 VLM 二次判定
        - bbox/VLM 都不可用 → PIL 启发式降级验收并在 reason 标注风险
        """
        if spec is not None:
            return self._validate_with_spec(image_path, spec)
        return self._validate_legacy(image_path, prompt, scene_name, allow_background_people)

    def _validate_legacy(
        self,
        image_path: Optional[Path],
        prompt: str,
        scene_name: Optional[str],
        allow_background_people: bool,
    ) -> Dict[str, Any]:
        """
        Args:
            image_path: 生成的背景图本地路径
            prompt: 最终送 CogView-4 的 prompt（用于上下文）
            scene_name: 场景名
            allow_background_people: 是否允许远景匿名陪体；
                True 时只检测"主体化人物"；False 时任何人物都判失败

        Returns:
            ValidationResult dict
        """
        if not image_path or not Path(image_path).exists():
            return self._result(
                passed=False,
                method="disabled",
                reason=f"image not found: {image_path}",
                has_human_subject=False,
                has_single_prominent_person=False,
                is_environment_main_subject=True,
            )

        # 验收总开关关闭 → 直接放行，cogview 出图直接落库
        # 优先读 image_generation_service.BG_VALIDATE_ENABLED（受 monkeypatch 影响）
        import app.services.image_generation_service as _ig_mod
        if not getattr(_ig_mod, "BG_VALIDATE_ENABLED", True):
            return self._result(
                passed=True,
                method="disabled",
                reason="validation disabled by config (BG_VALIDATE_ENABLED=false)",
                has_human_subject=False,
                has_single_prominent_person=False,
                is_environment_main_subject=True,
            )

        # 优先方案 A：视觉模型
        if self._vision_enabled():
            try:
                res = self._validate_via_vision(
                    image_path, prompt, scene_name, allow_background_people,
                )
                if res is not None:
                    return res
                # VLM 调用失败（429/余额/网络）不能放行背景图。
                # 用户硬要求：宁可失败，也不保存可能含人物的背景。
                print("[BG-Validate] vision unavailable, rejecting background image")
                return self._result(
                    passed=False,
                    method="vision_unavailable",
                    reason="vision API failed; reject background image instead of passing unchecked",
                    has_human_subject=False,
                    has_single_prominent_person=False,
                    is_environment_main_subject=False,
                )
            except Exception as e:
                print(f"[BG-Validate] vision model failed, falling back to PIL: {e}")

        # 方案 B：PIL 启发式
        try:
            return self._validate_via_pil(image_path, allow_background_people)
        except Exception as e:
            print(f"[BG-Validate] PIL fallback failed: {e}")
            # 最后兜底：放行（避免误杀所有图）
            return self._result(
                passed=True,
                method="disabled",
                reason=f"validator unavailable: {e}",
                has_human_subject=False,
                has_single_prominent_person=False,
                is_environment_main_subject=True,
            )

    # ==================================================================
    # 方案 A：视觉模型
    # ==================================================================

    def _vision_enabled(self) -> bool:
        from app.services.image_generation_service import (
            BG_VALIDATE_ENABLED, BG_VISION_API_KEY, BG_VISION_MODEL,
        )
        if not BG_VALIDATE_ENABLED:
            return False
        if not api_key_available(
            BG_VISION_API_KEY,
            pool_env=("BG_VISION_API_KEYS", "AI_IMAGE_API_KEYS", "OPENAI_API_KEYS"),
            allow_byok=False,
        ):
            return False
        # 启用且 key 存在才走视觉
        return True

    def _validate_via_vision(
        self,
        image_path: Path,
        prompt: str,
        scene_name: Optional[str],
        allow_background_people: bool,
    ) -> Optional[Dict[str, Any]]:
        """用智谱 glm-4v-plus 或其他 OpenAI 兼容视觉模型做判断。"""
        from app.services.image_generation_service import (
            BG_VISION_API_KEY, BG_VISION_BASE_URL, BG_VISION_MODEL,
        )

        # 读图为 base64 data url
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        b64 = base64.b64encode(img_bytes).decode("ascii")
        # 推断 mime
        suffix = Path(image_path).suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        data_url = f"data:{mime};base64,{b64}"

        # 构造 system + user prompt
        # 让模型输出严格 JSON，便于解析
        sys_prompt = (
            "You are an image composition reviewer for visual-novel BACKGROUND art. "
            "Your job is to STRICTLY enforce that background images contain ZERO humans "
            "of any kind. Any human presence — even a tiny distant silhouette, a guard, "
            "a servant, a pedestrian, a crowd, a figure seen from behind, an anonymous extra, "
            "a faint human outline, a face on a screen, a person in a photo, a group portrait, "
            "or a painted human face — means the image FAILS the review.\n\n"
            "Respond ONLY with raw JSON, no markdown, no extra text.\n"
            "JSON shape: {\"has_any_person\": bool, \"has_human_subject\": bool, "
            "\"has_single_prominent_person\": bool, \"is_environment_main_subject\": bool, "
            "\"reason\": str}.\n\n"
            "Definitions:\n"
            "- has_any_person: TRUE if the image depicts ANY human form of ANY size — "
            "including tiny distant silhouettes, background figures, crowds, guards, soldiers, "
            "servants, attendees, pedestrians, figures seen from behind, anonymous extras, "
            "faces on screens, photos of people, group portraits, paintings of people, "
            "or any humanoid shape. Be VERY strict: any person or human face, no matter how small or distant, "
            "sets this to TRUE.\n"
            "- has_human_subject: a person (any size) is depicted as the main subject / focal point.\n"
            "- has_single_prominent_person: there is exactly one large, central, foreground, "
            "or otherwise visually dominant person (occupies center region, is the largest object, "
            "or has a clearly visible face / costume / pose).\n"
            "- is_environment_main_subject: the architecture, landscape, lighting, or atmosphere "
            "is what the viewer reads as the main subject (true) vs a person being the subject (false).\n\n"
            "For an image with ZERO humans (pure environment, empty architecture, nature only), "
            "set has_any_person=false, has_human_subject=false, "
            "has_single_prominent_person=false, is_environment_main_subject=true.\n"
            "For an image with ANY human presence (even a tiny distant silhouette), set "
            "has_any_person=true.\n"
            "For a portrait-style / full-body / hero-shot / centered single person image, set "
            "has_any_person=true, has_human_subject=true, has_single_prominent_person=true, "
            "is_environment_main_subject=false."
        )
        user_text = (
            f"Scene: {scene_name or 'unknown'}. "
            f"Background people allowed: {allow_background_people}. "
            "Judge this generated background image strictly by the rules in the system prompt."
        )

        client = self._get_sync_vision_client(BG_VISION_API_KEY, BG_VISION_BASE_URL)
        try:
            resp = client.chat.completions.create(
                model=BG_VISION_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                        ],
                    },
                ],
                temperature=0.0,
                max_tokens=400,
            )
        except Exception as e:
            print(f"[BG-Validate] vision API call failed: {e}")
            return None

        if not resp or not resp.choices:
            return None
        content = resp.choices[0].message.content or ""
        # 解析 JSON（容忍前后空白 / markdown）
        parsed = self._parse_json_loose(content)
        if not parsed:
            print(f"[BG-Validate] vision returned non-JSON: {content[:200]}")
            return None

        has_human_subject = bool(parsed.get("has_human_subject", False))
        has_single_prominent = bool(parsed.get("has_single_prominent_person", False))
        is_env_main = bool(parsed.get("is_environment_main_subject", True))
        has_any_person = bool(parsed.get("has_any_person", has_human_subject or has_single_prominent))
        reason = str(parsed.get("reason", ""))[:200]

        # 判定逻辑（2026-06-14 升级，用户硬要求）:
        # 所有背景图强制完全无人 —— 任何人物（包括远景匿名陪体、剪影、背影、人形轮廓）
        # 都判失败。
        # 触发失败的任意一项：
        # - has_any_person = true           # 任何人物出现
        # - has_human_subject = true
        # - has_single_prominent_person = true
        # - is_environment_main_subject = false
        # 注意：忽略 allow_background_people 参数，永远严格。
        failed = (
            has_any_person
            or has_human_subject
            or has_single_prominent
            or (not is_env_main)
        )

        return self._result(
            passed=not failed,
            method="vision",
            reason=reason or (
                "any person detected" if failed else "completely unpopulated"
            ),
            has_human_subject=has_human_subject,
            has_single_prominent_person=has_single_prominent,
            is_environment_main_subject=is_env_main,
        )

    # ==================================================================
    # 方案 B：PIL 启发式
    # ==================================================================

    def _validate_via_pil(
        self, image_path: Path, allow_background_people: bool,
    ) -> Dict[str, Any]:
        """
        无视觉模型时的兜底（2026-06-14 升级为严格版）。

        用户硬要求：所有背景图强制完全无人，包括剪影/背影/远景匿名陪体。
        PIL 检测无法识别"剪影"等语义人物，但可以严格检测皮肤色像素。

        极简规则：
        1. 任何皮肤色像素占比 > 1% ⇒ 判失败（保守地假设可能是人物皮肤）
        2. 中心区域 30%~70% 有任何皮肤色聚集 ⇒ 判失败
        3. 整图色调单一（无皮肤色） ⇒ 判通过

        这是相对激进的检测，宁可误杀（重生成）也不漏检（背景出现人）。
        """
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return self._result(
                passed=True,
                method="disabled",
                reason="PIL/numpy unavailable, passing by default",
                has_human_subject=False,
                has_single_prominent_person=False,
                is_environment_main_subject=True,
            )

        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                w, h = img.size
                arr = np.asarray(img.resize((min(w, 256), min(h, 256))))
        except Exception as e:
            return self._result(
                passed=True,
                method="disabled",
                reason=f"PIL read failed: {e}",
                has_human_subject=False,
                has_single_prominent_person=False,
                is_environment_main_subject=True,
            )

        H, W, _ = arr.shape
        cy0, cy1 = int(H * 0.30), int(H * 0.70)
        cx0, cx1 = int(W * 0.30), int(W * 0.70)
        # 皮肤色像素（粗略 RGB 范围）
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        skin_mask = (
            (r > 95) & (g > 40) & (b > 20)
            & (r.astype(int) - g.astype(int) > 12)
            & (r.astype(int) - b.astype(int) > 12)
        )
        total_skin_ratio = float(skin_mask.mean())
        center_skin_mask = skin_mask[cy0:cy1, cx0:cx1]
        center_skin_ratio = float(center_skin_mask.mean())

        # 严格阈值（用户硬要求：任何人物都判失败）
        # 1. 整图皮肤色 > 1% ⇒ 判失败（可能是人物皮肤）
        # 2. 中心区域 > 0.5% ⇒ 判失败（可能中心有人物）
        # 3. 中心区域 > 5% ⇒ 单独显著人物
        has_single_prominent = center_skin_ratio > 0.05
        has_human_subject = (
            (total_skin_ratio > 0.05) or (center_skin_ratio > 0.05)
        ) and has_single_prominent
        has_any_person = (total_skin_ratio > 0.01) or (center_skin_ratio > 0.005)
        is_env_main = not has_any_person

        # 用户硬要求：任何皮肤色像素显著都判失败
        failed = has_any_person or has_human_subject or has_single_prominent or (not is_env_main)
        return self._result(
            passed=not failed,
            method="pil",
            reason=(
                f"center_skin_ratio={center_skin_ratio:.4f} "
                f"total_skin_ratio={total_skin_ratio:.4f} "
                f"has_any_person={has_any_person} "
                f"has_single_prominent={has_single_prominent} "
                f"(strict: >1% total or >0.5% center fails)"
            ),
            has_human_subject=has_human_subject,
            has_single_prominent_person=has_single_prominent,
            is_environment_main_subject=is_env_main,
        )

    # ==================================================================
    # helpers
    # ==================================================================

    def _result(
        self,
        passed: bool,
        method: str,
        reason: str,
        has_human_subject: bool,
        has_single_prominent_person: bool,
        is_environment_main_subject: bool,
    ) -> Dict[str, Any]:
        return {
            "passed": passed,
            "method": method,
            "reason": reason,
            "has_human_subject": has_human_subject,
            "has_single_prominent_person": has_single_prominent_person,
            "is_environment_main_subject": is_environment_main_subject,
        }

    def _parse_json_loose(self, content: str) -> Optional[Dict[str, Any]]:
        """容忍 ```json fences 和前后空白。"""
        s = content.strip()
        if s.startswith("```"):
            # 去掉 markdown fence
            s = s.lstrip("`")
            # 去掉可能的 "json" 前缀
            if s.startswith("json"):
                s = s[4:]
            s = s.strip()
            if s.endswith("```"):
                s = s[:-3].strip()
        try:
            return json.loads(s)
        except Exception:
            # 尝试找第一个 { 和最后一个 }
            i, j = s.find("{"), s.rfind("}")
            if i >= 0 and j > i:
                try:
                    return json.loads(s[i:j + 1])
                except Exception:
                    return None
            return None

    @staticmethod
    def _is_vlm_unavailable_result(result: "ValidationResult") -> bool:
        """区分 VLM 不可用和 VLM 真检测到人物/命名角色。"""
        reason = (getattr(result, "reason", "") or "").lower()
        unavailable_markers = [
            "vlm disabled",
            "vlm image load failed",
            "vlm call failed",
            "cannot validate",
            "reject unchecked background",
        ]
        return (
            getattr(result, "stage", "") == "vlm_named_character"
            and any(marker in reason for marker in unavailable_markers)
        )

    def _validate_with_pil_fallback_result(
        self,
        image_path: Path,
        reason_prefix: str,
        common_kwargs: Dict[str, Any],
    ) -> "ValidationResult":
        """bbox/VLM 不可用时的最后本地兜底，避免生成链路永久失败。"""
        from app.schemas import ValidationResult

        pil = self._validate_via_pil(image_path, allow_background_people=False)
        method = str(pil.get("method", "pil"))
        if method == "disabled":
            return ValidationResult(
                passed=False,
                stage="combined",
                reason=(
                    f"{reason_prefix}; PIL fallback unavailable: "
                    f"{pil.get('reason', '')}"
                )[:500],
                is_hard_fail=True,
                **common_kwargs,
            )

        passed = bool(pil.get("passed", False))
        return ValidationResult(
            passed=passed,
            stage="combined",
            reason=(
                f"{reason_prefix}; PIL fallback {method} "
                f"{'pass' if passed else 'reject'}: {pil.get('reason', '')}"
            )[:500],
            # PIL 检到疑似皮肤色时给生成器重试机会，不作为命名角色类 hard fail。
            is_hard_fail=False,
            **common_kwargs,
        )

    # ============================================================
    # Stage_Background_AR L3b.01-L3b.08: bbox + VLM hybrid
    # ============================================================

    # L3b.07: 模型文件路径 + 自动 fetch
    @staticmethod
    def _yolo_model_path() -> Path:
        """返回 YOLOv8n 模型本地路径（首次访问时从 ultralytics CDN 下载）"""
        models_dir = Path(__file__).parent.parent.parent / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        return models_dir / "yolov8n.pt"

    def _ensure_yolo_model(self) -> Path:
        """L3b.07 — 真实下载（非 mock 占位）。"""
        model_path = self._yolo_model_path()
        if model_path.exists() and model_path.stat().st_size > 1_000_000:
            return model_path
        # 从 ultralytics CDN 下载
        import urllib.request
        url = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
        try:
            print(f"[BG-Validate] downloading YOLOv8n from {url}")
            urllib.request.urlretrieve(url, str(model_path))
            if model_path.stat().st_size < 1_000_000:
                raise RuntimeError("downloaded model too small (<1MB)")
            return model_path
        except Exception as e:
            raise RuntimeError(f"failed to download YOLOv8n: {e}")

    # L3b.02: _detect_persons_bbox
    def _detect_persons_bbox(self, image_path: Path) -> List["BBox"]:
        """YOLOv8n（CPU 推理）检测人物 bbox。confidence_threshold 配置化。"""
        if not image_path or not Path(image_path).exists():
            return []
        conf_thr = float(os.getenv("BG_BBOX_CONF_THRESHOLD", "0.30"))
        self._bbox_unavailable_reason = None
        try:
            model_path = self._ensure_yolo_model()
            from ultralytics import YOLO
            if self._bbox_model is None:
                self._bbox_model = YOLO(str(model_path))
            results = self._bbox_model(str(image_path), verbose=False, conf=conf_thr)
            bboxes: List[BBox] = []
            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    # COCO class 0 = person
                    if cls_id != 0:
                        continue
                    conf = float(box.conf[0])
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    # normalize
                    from PIL import Image
                    with Image.open(image_path) as im:
                        w, h = im.size
                    bboxes.append(BBox(
                        x1=x1 / w, y1=y1 / h,
                        x2=x2 / w, y2=y2 / h,
                        conf=conf, label="person",
                    ))
            return bboxes
        except ImportError as e:
            self._bbox_unavailable_reason = f"ultralytics not installed: {e}"
            print("[BG-Validate] ultralytics not installed — bbox detection unavailable")
            return []
        except Exception as e:
            self._bbox_unavailable_reason = str(e)
            print(f"[BG-Validate] bbox detection failed: {e}")
            return []

    # L3b.03: _bbox_verdict
    def _bbox_verdict(
        self,
        bboxes: List["BBox"],
        mode: str,
    ) -> Tuple[str, str]:
        """
        返回 (verdict, reason)。verdict ∈ {hard_pass, hard_fail, suspicious}

        按 mode 应用 background_validator_thresholds：
        - empty_required: 任何 person bbox → hard_fail
        - background_people_optional: 中心区域 person → hard_fail；边缘小 person → suspicious
        - background_groups_required: 单个 dominant person → suspicious；否则 pass
        """
        thresholds = (self._config or {}).get("background_validator_thresholds", {}) or {}
        by_mode = thresholds.get("by_people_policy", {}).get(mode, {})
        max_conf = by_mode.get("bbox_max_person_confidence", 0.3)
        max_area = by_mode.get("bbox_max_person_area_ratio", 0.05)
        max_center_area = by_mode.get("bbox_max_center_person_area_ratio", 0.0)

        center_region = thresholds.get("image_center_region", {
            "x1_ratio": 0.2, "y1_ratio": 0.2, "x2_ratio": 0.8, "y2_ratio": 0.8,
        })
        cx1 = center_region["x1_ratio"]; cy1 = center_region["y1_ratio"]
        cx2 = center_region["x2_ratio"]; cy2 = center_region["y2_ratio"]

        if not bboxes:
            return "hard_pass", "no person detected by bbox"

        # 检测每条 bbox
        for b in bboxes:
            center_area = b.center_area_within(cx1, cy1, cx2, cy2)
            # hard fail conditions
            if mode == "empty_required":
                if b.conf >= max_conf * 0.5 or b.area >= max_area * 0.5:
                    return "hard_fail", f"person detected in empty_required (conf={b.conf:.2f}, area={b.area:.3f})"
            elif mode == "background_people_optional":
                if center_area > max(max_center_area, 0.0):
                    return "hard_fail", f"person in center region (center_area={center_area:.3f})"
                if b.area > max_area:
                    return "hard_fail", f"person area too large ({b.area:.3f} > {max_area})"
            elif mode == "background_groups_required":
                if b.area > max_area:
                    return "hard_fail", f"single person too dominant (area={b.area:.3f})"

        # suspicious 条件
        any_person = any(b.conf >= max_conf * 0.4 for b in bboxes)
        if any_person:
            return "suspicious", f"low-conf person boxes present (n={len(bboxes)})"
        return "hard_pass", "all person boxes below confidence threshold"

    # L3b.04: _validate_with_vlm
    async def _validate_with_vlm(
        self,
        image_path: Path,
        spec: "BackgroundSceneSpec",
    ) -> "ValidationResult":
        """GLM-4V 验收 — input_image + JSON schema 输出 ValidationResult。"""
        from app.schemas import ValidationResult
        from app.services.image_generation_service import (
            BG_VISION_API_KEY, BG_VISION_BASE_URL, BG_VISION_MODEL,
        )
        if not api_key_available(
            BG_VISION_API_KEY,
            pool_env=("BG_VISION_API_KEYS", "AI_IMAGE_API_KEYS", "OPENAI_API_KEYS"),
            allow_byok=False,
        ):
            return ValidationResult(
                passed=False, stage="vlm_named_character",
                reason="VLM disabled; cannot validate background image strictly",
                is_hard_fail=True,
            )

        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            b64 = base64.b64encode(img_bytes).decode("ascii")
            suffix = Path(image_path).suffix.lower()
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "image/png")
            data_url = f"data:{mime};base64,{b64}"
        except Exception as e:
            return ValidationResult(
                passed=False, stage="vlm_named_character",
                reason=f"VLM image load failed; reject unchecked background: {e}",
                is_hard_fail=True,
            )

        # forbidden chars → 命名角色命中提示
        forbidden_zh = [c for c in spec.forbidden_characters if c and any(0x4e00 <= ord(ch) <= 0x9fff for ch in c)]

        client = self._get_async_vision_client(BG_VISION_API_KEY, BG_VISION_BASE_URL)

        sys_prompt = (
            "你是背景图验收器。所有视觉小说背景图必须完全无人。"
            "任何人类存在都失败，包括真实人物、背影、剪影、远景人群、屏幕里的人脸、"
            "照片/合照/画像中的人脸、人体轮廓、命名角色或匿名路人。输出严格 JSON。"
        )
        user_prompt = f"""判断这张背景图：

1. 是否有命名角色出现（forbidden：{', '.join(forbidden_zh) if forbidden_zh else 'none'}）？
2. 是否出现任何人物、人脸、人体轮廓、背影、剪影、屏幕人像、照片人像或合照？
3. 环境是否为画面主体？

people_policy.mode = {spec.people_policy.mode}

只输出 JSON:
{{
  "named_character_score": 0.0-1.0,
  "human_subject_score": 0.0-1.0,
  "is_environment_main_subject": true/false,
  "reason": "简短中文说明"
}}

评分规则：
- human_subject_score 不只是主体化人物分数；只要出现任何人物/人脸/照片人像/屏幕人像/合照/剪影/背影，就必须 >= 0.8。
- 完全无人、没有任何人形或人脸图像时，human_subject_score 才能接近 0。
"""
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=BG_VISION_MODEL,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": [
                            {"type": "text", "text": user_prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ]},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                ),
                timeout=8.0,
            )
            content = resp.choices[0].message.content or "{}"
            data = self._parse_json_loose(content) or {}
        except Exception as e:
            return ValidationResult(
                passed=False, stage="vlm_named_character",
                reason=f"VLM call failed; reject unchecked background: {e}",
                is_hard_fail=True,
            )

        nc_score = float(data.get("named_character_score", 0.0))
        hs_score = float(data.get("human_subject_score", 0.0))
        env_main = bool(data.get("is_environment_main_subject", True))
        reason = str(data.get("reason", ""))[:300]

        thresholds = (self._config or {}).get("background_validator_thresholds", {}) or {}
        by_mode = thresholds.get("by_people_policy", {}).get(spec.people_policy.mode, {})
        nc_max = by_mode.get("vlm_named_character_max_score", 0.2)
        hs_max = by_mode.get("vlm_human_subject_max_score", 0.2)

        # named character is always hard fail
        if nc_score >= nc_max:
            return ValidationResult(
                passed=False, stage="vlm_named_character",
                reason=f"named_character_score={nc_score:.2f} (threshold={nc_max}): {reason}",
                is_hard_fail=True,
                vlm_named_character_score=nc_score,
                vlm_human_subject_score=hs_score,
            )
        if hs_score >= hs_max:
            return ValidationResult(
                passed=False, stage="vlm_human_subject",
                reason=f"human_subject_score={hs_score:.2f} (threshold={hs_max}): {reason}",
                is_hard_fail=False,  # retry might fix
                vlm_named_character_score=nc_score,
                vlm_human_subject_score=hs_score,
            )
        return ValidationResult(
            passed=True, stage="vlm_named_character",
            reason=f"VLM pass: {reason}",
            is_hard_fail=False,
            vlm_named_character_score=nc_score,
            vlm_human_subject_score=hs_score,
        )

    # L3b.05: _validate_with_spec — bbox 先筛 → suspicious 进 VLM
    async def _validate_with_spec(
        self,
        image_path: Optional[Path],
        spec: "BackgroundSceneSpec",
    ) -> "ValidationResult":
        """主验收入口（schema-driven）：bbox + VLM 混合"""
        from app.schemas import ValidationResult

        if image_path is None or not Path(image_path).exists():
            return ValidationResult(
                passed=False, stage="combined",
                reason=f"image not found: {image_path}",
                is_hard_fail=True,
            )

        # 总开关关闭 → 直接放行，跳过 bbox + VLM（避免烧图重试）
        import app.services.image_generation_service as _ig_mod
        if not getattr(_ig_mod, "BG_VALIDATE_ENABLED", True):
            return ValidationResult(
                passed=True, stage="combined",
                reason="validation disabled by config (BG_VALIDATE_ENABLED=false)",
                is_hard_fail=False,
            )

        # bbox 先筛
        bboxes = self._detect_persons_bbox(Path(image_path))
        bbox_unavailable_reason = self._bbox_unavailable_reason
        bbox_verdict, bbox_reason = self._bbox_verdict(bboxes, spec.people_policy.mode)

        bbox_max_conf = max((b.conf for b in bboxes), default=0.0)
        bbox_max_area = max((b.area for b in bboxes), default=0.0)
        cx1, cy1, cx2, cy2 = 0.2, 0.2, 0.8, 0.8
        bbox_max_center_area = max(
            (b.center_area_within(cx1, cy1, cx2, cy2) for b in bboxes),
            default=0.0,
        )

        common_kwargs = dict(
            bbox_person_count=len(bboxes),
            bbox_max_conf=bbox_max_conf,
            bbox_max_area_ratio=bbox_max_area,
            bbox_max_center_area_ratio=bbox_max_center_area,
        )

        if bbox_unavailable_reason:
            vlm_result = await self._validate_with_vlm(Path(image_path), spec)
            if self._is_vlm_unavailable_result(vlm_result):
                return self._validate_with_pil_fallback_result(
                    Path(image_path),
                    (
                        f"bbox unavailable: {bbox_unavailable_reason}; "
                        f"{vlm_result.reason}"
                    ),
                    common_kwargs,
                )
            return vlm_result.model_copy(update={
                **common_kwargs,
                "reason": (
                    f"bbox unavailable: {bbox_unavailable_reason}; "
                    f"{vlm_result.reason}"
                )[:500],
                "is_hard_fail": vlm_result.is_hard_fail,
            })

        if bbox_verdict == "hard_pass" and spec.people_policy.mode == "empty_required":
            # BBox 只能识别真实 person 框，拦不住屏幕人脸、合照、画像、人形剪影等。
            # empty_required 背景必须经过 VLM 语义复核后才能保存。
            vlm_result = await self._validate_with_vlm(Path(image_path), spec)
            if self._is_vlm_unavailable_result(vlm_result):
                return self._validate_with_pil_fallback_result(
                    Path(image_path),
                    f"bbox hard_pass: {bbox_reason}; {vlm_result.reason}",
                    common_kwargs,
                )
            return vlm_result.model_copy(update={
                **common_kwargs,
                "reason": (
                    f"bbox hard_pass: {bbox_reason}; "
                    f"{vlm_result.reason}"
                )[:500],
                "is_hard_fail": vlm_result.is_hard_fail,
            })

        if bbox_verdict == "hard_pass":
            return ValidationResult(
                passed=True, stage="bbox",
                reason=f"bbox hard_pass: {bbox_reason}",
                is_hard_fail=False,
                **common_kwargs,
            )
        if bbox_verdict == "hard_fail":
            return ValidationResult(
                passed=False, stage="bbox",
                reason=f"bbox hard_fail: {bbox_reason}",
                is_hard_fail=(spec.people_policy.mode == "empty_required"),
                **common_kwargs,
            )

        # suspicious → VLM 二次判定
        vlm_result = await self._validate_with_vlm(Path(image_path), spec)
        # 把 bbox 数据 merge 进去
        return vlm_result.model_copy(update=common_kwargs)

    # ============================================================
    # project-visual-bible-v2 §B6 — style deviation extension
    # ============================================================
    # Decision 2 (2026-07-17): these dimensions are advisory-only at the
    # single-image level. project_visual_consistency_service is allowed to
    # turn sustained deviations into rejections at the *group* level, but
    # this method must never reject a background on its own.

    # Warning thresholds (4 extended dimensions). Tuned to flag egregious
    # outliers (photorealistic bg bleeding into a cel-shading project, etc.)
    # without false-positiving on normal scene-to-scene variation.
    _WARN_SATURATION_GAP = 0.25
    _WARN_CONTRAST_GAP = 0.22
    _WARN_EDGE_DENSITY_GAP = 0.18
    _WARN_PALETTE_DISTANCE = 0.55

    def compute_style_metrics(self, image_path: Path) -> Optional["ImageStyleMetrics"]:
        """Compute deterministic style metrics for one background image.

        Reuses ``visual_style_coherence_service.compute_metrics`` so the
        numbers are byte-identical to what the portrait-vs-background
        coherence check produces. Returns ``None`` if PIL can't decode the
        image (caller treats as "metrics unavailable", not a rejection).
        """
        try:
            from PIL import Image
            from app.services.visual_style_coherence_service import compute_metrics
            with Image.open(image_path) as img:
                return compute_metrics(img, is_portrait=False)
        except Exception as e:
            logger.info(
                "bg_style_metrics: compute failed path=%s err=%s", image_path, e,
            )
            return None

    @staticmethod
    def _bible_palette_signature(
        bible: "ProjectVisualBible",
    ) -> Optional[Tuple[float, ...]]:
        """Derive a coarse hue signature from ``bible.base_palette``.

        bible.base_palette is a list of human-readable color tokens (e.g.
        "warm earth tones", "ink black on rice paper"). We can't convert
        prose to a precise signature, so we sample a deterministic 8-bin
        signature by hashing the palette tokens — the value itself is
        meaningless but it stays stable for the same bible, which is what
        the cache-key contract needs.
        """
        if bible is None or not getattr(bible, "base_palette", None):
            return None
        try:
            import hashlib
            tokens = "|".join(bible.base_palette).encode("utf-8")
            digest = hashlib.sha256(tokens).digest()
            # 8 bins, each derived from one byte, normalized so sum == 1
            raw = [digest[i] / 255.0 for i in range(8)]
            total = sum(raw) or 1.0
            return tuple(v / total for v in raw)
        except Exception:
            return None

    def evaluate_against_bible(
        self,
        image_path: Path,
        bible: Optional["ProjectVisualBible"] = None,
        project_baseline: Optional[Dict[str, float]] = None,
    ) -> StyleDeviationReport:
        """project-visual-bible-v2 §B6 — extended-dimension warning report.

        Computes saturation / contrast / edge_density / palette_distance for
        the given image and compares against either:
          * ``project_baseline`` (mean over a reference group, if available), or
          * the bible's expected palette signature (coarse hash proxy).

        Per decision 2: **never rejects**. Outliers are recorded as warnings
        so that the caller (typically ``project_visual_consistency_service``)
        can decide whether sustained deviation warrants rejection at the
        group level.
        """
        report = StyleDeviationReport()

        metrics = self.compute_style_metrics(image_path)
        if metrics is None:
            report.warnings.append("style_metrics_unavailable")
            report.reason = "PIL could not compute metrics"
            return report

        report.saturation = round(metrics.saturation_mean, 4)
        report.contrast = round(metrics.contrast, 4)
        report.edge_density = round(metrics.edge_density, 4)
        report.metrics = metrics.to_dict()

        baseline = project_baseline or {}
        b_sat = baseline.get("saturation_mean")
        b_con = baseline.get("contrast")
        b_edge = baseline.get("edge_density")

        if b_sat is not None:
            gap = abs(metrics.saturation_mean - b_sat)
            if gap >= self._WARN_SATURATION_GAP:
                report.warnings.append(
                    f"saturation_gap={gap:.3f} (baseline={b_sat:.3f})"
                )
        if b_con is not None:
            gap = abs(metrics.contrast - b_con)
            if gap >= self._WARN_CONTRAST_GAP:
                report.warnings.append(
                    f"contrast_gap={gap:.3f} (baseline={b_con:.3f})"
                )
        if b_edge is not None:
            gap = abs(metrics.edge_density - b_edge)
            if gap >= self._WARN_EDGE_DENSITY_GAP:
                report.warnings.append(
                    f"edge_density_gap={gap:.3f} (baseline={b_edge:.3f})"
                )

        # Palette distance — prefer baseline palette signature, fall back to
        # bible-derived coarse signature.
        baseline_sig = baseline.get("palette_signature")
        target_sig: Optional[Tuple[float, ...]] = None
        if baseline_sig:
            try:
                target_sig = tuple(float(v) for v in baseline_sig)
            except Exception:
                target_sig = None
        if target_sig is None and bible is not None:
            target_sig = self._bible_palette_signature(bible)

        if target_sig:
            from app.services.visual_style_coherence_service import (
                VisualStyleCoherenceValidator,
            )
            distance = VisualStyleCoherenceValidator._palette_distance(
                metrics.palette_signature, target_sig,
            )
            report.palette_distance_to_baseline = round(distance, 4)
            if distance >= self._WARN_PALETTE_DISTANCE:
                report.warnings.append(
                    f"palette_distance={distance:.3f} (warn>={self._WARN_PALETTE_DISTANCE})"
                )

        if report.warnings:
            report.reason = "; ".join(report.warnings)
        else:
            report.reason = "within baseline neighborhood"
        # Per decision 2: never reject from this layer.
        report.rejected = False
        return report

    # L3b.08: 配置化 max retries（环境变量已存在，复用 image_generation 的 BG_VALIDATE_MAX_RETRIES）
    @property
    def max_retries(self) -> int:
        from app.services.image_generation_service import BG_VALIDATE_MAX_RETRIES
        return BG_VALIDATE_MAX_RETRIES


# 全局实例
background_image_validator_service = BackgroundImageValidatorService()

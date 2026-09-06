"""
素材管理服务 - 管理素材生成流程和数据库操作
"""
import os
import asyncio
import hashlib
import time
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy.orm import Session
from app.models import Asset, Project, StoryBible, ChapterOutline, ChapterContent
from app.services.image_generation_service import (
    PORTRAIT_GENERATION_CONTRACT_VERSION,
    image_generation_service,
)
from app.services.prompt_builder_service import prompt_builder_service
from app.services.prompt_rewriter_service import (
    PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION,
)
from app.services.generation_stat_service import GenerationStatService
from app.services.visual_style_profile_service import (
    VisualStyleProfile,
    visual_style_profile_service,
)
from app.services.character_visual_profile_service import normalize_character
from app.services.character_age_contract import (
    age_contract_from_profile,
    apply_age_contract_to_appearance,
    apply_age_contract_to_character,
    build_age_contract,
    identity_variant_id,
)
from app.utils import logging as xlog
from app.services.scene_segmenter_service import scene_segmenter_service

# project-visual-bible-v2 §B6: locked-bible stamp + presentation pipeline
from app.services.project_visual_bible_service import (
    project_visual_bible_service,
    ProjectVisualBible,
    VERSION as PROJECT_VISUAL_BIBLE_VERSION,
)
from app.services.source_visual_profile_service import source_visual_profile_service
from app.services.canonical_identity_service import canonical_identity_prompt
from app.services.portrait_prompt_identity_service import build_portrait_rewriter_identity
from app.services.asset_prompt_builder_service import (
    VERSION as ASSET_PROMPT_CONTRACT_VERSION,
    scene_visual_seed,
)
from app.services.background_image_validator_service import (
    BACKGROUND_VALIDATION_VERSION,
)
from app.services.portrait_normalization_service import (
    PORTRAIT_NORMALIZATION_VERSION,
    default_normalizer,
)
from app.services.background_style_classifier_service import (
    BackgroundStyleClassifierService,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.services.character_identity_contract import CharacterIdentityContract

# P1.3 — 回退开关：PORTRAIT_AUTO_DEMAND=0 一键关掉内容驱动立绘，回到旧用户选项模式。
PORTRAIT_AUTO_DEMAND_ENABLED = os.getenv("PORTRAIT_AUTO_DEMAND", "1") != "0"
KEYFRAME_AUTO_SELECT_MAX = int(os.getenv("KEYFRAME_AUTO_SELECT_MAX", "3"))
KEYFRAME_IDENTITY_PAID_RETRY_HARD_LIMIT = 1

# project-visual-bible-v2 §B6 — transient registry for per-scene treatment.
# BackgroundSceneSpec is a pydantic model that ignores extra attributes, so we
# can't stash ``scene_treatment`` on the spec itself. Instead the v2 generator
# registers the SceneVisualTreatment under spec.scene_selector and
# ``_persist_background_asset`` looks it up by the same key.
_SCENE_TREATMENT_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _register_scene_treatment(scene_selector: str, treatment: Any) -> None:
    if not scene_selector:
        return
    try:
        _SCENE_TREATMENT_REGISTRY[scene_selector] = treatment.to_dict() if hasattr(treatment, "to_dict") else dict(treatment)
    except Exception:
        pass


def _pop_scene_treatment(scene_selector: str) -> Optional[Dict[str, Any]]:
    if not scene_selector:
        return None
    return _SCENE_TREATMENT_REGISTRY.pop(scene_selector, None)


class AssetManagementService:
    """素材管理服务"""

    def __init__(self, db: Session):
        self.db = db
        self.stat_service = GenerationStatService(db)

    # ------------------------------------------------------------------
    # project-visual-bible-v2 §B6 — locked-bible helpers
    # ------------------------------------------------------------------

    def _ensure_visual_bible_locked(
        self,
        project_id: int,
        story_bible: Optional[StoryBible],
    ) -> Optional[ProjectVisualBible]:
        """Build (if needed) and assert-lock the project visual bible.

        Returns the locked bible, or ``None`` if ``story_bible`` is missing
        (caller already handles that case as a hard error upstream).

        Hard constraint (plan §A): project-level style is locked once and
        cannot be rewritten at chapter level. Calling this at every
        generate_* entry point enforces the invariant — if the bible is
        already locked, ``assert_bible_locked`` is a no-op; if not yet
        locked, ``build_for_project`` creates and persists it idempotently.
        """
        if story_bible is None:
            return None
        project = self.db.query(Project).filter(Project.id == project_id).first()
        bible = project_visual_bible_service.build_for_project(
            story_bible,
            project_title=getattr(project, "title", "") or "",
            project_style=getattr(project, "style", "") or "",
            source_work=getattr(project, "source_work", "") or "",
            source_context=self._project_visual_context(project),
        )
        try:
            project_visual_bible_service.assert_bible_locked(story_bible)
            # raw_json is an ordinary JSON column, so assigning the merged dict
            # above is what makes SQLAlchemy track the lock.  Flush it in the
            # current transaction before any image request starts.
            self.db.flush()
        except Exception as e:
            xlog.warn(
                project_id,
                "[asset-mgr] assert_bible_locked failed project_id=%d err=%s",
                project_id, e,
            )
        return bible

    @staticmethod
    def _project_visual_context(project: Optional[Project]) -> str:
        if project is None:
            return ""
        return " ".join(
            str(value or "")
            for value in (
                project.title,
                project.story_start,
                project.story_end,
                project.extra_requirements,
            )
        )

    @staticmethod
    def _stamp_visual_bible_params(
        params: Dict[str, Any],
        bible: Optional[ProjectVisualBible],
        *,
        scene_selector: Optional[str] = None,
        project_id: int = 0,
        variant: int = 0,
        scene_treatment: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return ``params`` with project-visual-bible-v2 stamps merged in.

        Idempotent: overwrites the 6 stamp fields every call so cache-contract
        checks can rely on them being authoritative.
        """
        out = dict(params or {})
        if bible is not None:
            out["visual_bible_version"] = PROJECT_VISUAL_BIBLE_VERSION
            out["asset_prompt_contract_version"] = ASSET_PROMPT_CONTRACT_VERSION
            out["style_fingerprint"] = bible.fingerprint
            out["source_work"] = bible.source_work or None
            out["source_profile_id"] = bible.source_profile_id or None
            out["background_validation_version"] = BACKGROUND_VALIDATION_VERSION
            out["portrait_normalization_version"] = PORTRAIT_NORMALIZATION_VERSION
            if scene_selector:
                out["scene_visual_seed"] = scene_visual_seed(
                    project_id=project_id,
                    style_fingerprint=bible.fingerprint,
                    scene_selector=scene_selector,
                    variant=variant,
                )
        if scene_treatment is not None:
            out["scene_treatment"] = scene_treatment
        # Decision 2 (2026-07-17): validation always runs at the advisory
        # layer; this flag tracks whether the *full* validator executed
        # (False) or was skipped due to unavailable deps (True). Callers
        # that don't actually trigger the validator leave it False to
        # match the contract default.
        out.setdefault("validation_skipped", False)
        return out

    def _build_visual_style_profile(
        self,
        project_id: int,
        story_bible: Any,
    ) -> VisualStyleProfile:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        story_bible_row = (
            story_bible
            if isinstance(story_bible, StoryBible)
            else self._get_story_bible(project_id)
        )
        return visual_style_profile_service.build_profile(
            story_bible_row or story_bible or {},
            project_style=getattr(project, "style", "") or "",
            project_title=getattr(project, "title", "") or "",
            source_work=getattr(project, "source_work", "") or "",
            source_context=self._project_visual_context(project),
        )

    async def generate_all_portraits(
        self,
        project_id: int,
        variations: Optional[List[Dict[str, str]]] = None,
        batch_size: Optional[int] = None,
        auto_demand: bool = False
    ) -> Dict[str, Any]:
        """
        生成项目所有角色立绘

        Args:
            project_id: 项目 ID
            variations: 情绪/装束变体配置（auto_demand=True 时忽略）
            batch_size: 批量生成数量
            auto_demand: True 时调 PortraitDemandAnalyzer 从章节正文抽
                per-character 的 (emotion, outfit, pose) 变体，忽略外部 variations；
                LLM 失败 / 无章节内容时 fallback 到默认 5 档变体。
                受 env ``PORTRAIT_AUTO_DEMAND=0`` 全局关闭。

        Returns:
            {
                "total": int,
                "generated": int,
                "failed": int,
                "results": [...]
            }
        """
        if batch_size is None:
            # 默认 10：key 上限 15 并发，单批 10 张能吃满多数项目，仍留 5 给段间并行的 backgrounds/keyframe。
            # 显式传值的调用方仍能覆盖；旧默认 5 可通过 PORTRAIT_BATCH_SIZE=5 还原。
            try:
                batch_size = int(os.getenv("PORTRAIT_BATCH_SIZE", "10"))
            except ValueError:
                batch_size = 10
            if batch_size < 1:
                batch_size = 1
        xlog.info(project_id, "[asset-mgr] portraits start project_id=%d batch_size=%d auto_demand=%s", project_id, batch_size, auto_demand)

        # 获取 StoryBible
        story_bible = self._get_story_bible(project_id)
        if not story_bible:
            xlog.warn(project_id, "[asset-mgr] portraits missing story bible project_id=%d", project_id)
            return {"total": 0, "generated": 0, "failed": 0, "results": [], "error": "StoryBible 不存在"}

        sb_raw = dict(story_bible.raw_json or {})
        if not sb_raw.get("characters") and story_bible.characters:
            sb_raw["characters"] = story_bible.characters
        sb_raw = prompt_builder_service.enrich_story_bible_genders(
            sb_raw,
            context_text=self._build_character_gender_context(project_id, story_bible),
        )
        # project-visual-bible-v2 §B6 — lock the project bible at generate entry
        bible = self._ensure_visual_bible_locked(project_id, story_bible)
        visual_style_profile = self._build_visual_style_profile(project_id, story_bible)
        sb_raw = source_visual_profile_service.apply_character_overrides(
            sb_raw,
            visual_style_profile.source_profile_id,
        )

        # P1.3 — auto_demand 路径优先用 PortraitDemandAnalyzer 抽 per-character 变体。
        portrait_prompts: Optional[List[Dict[str, Any]]] = None
        demand_source = "legacy"
        if auto_demand and PORTRAIT_AUTO_DEMAND_ENABLED:
            try:
                portrait_prompts, demand_source = await self._build_auto_demand_portrait_prompts(
                    project_id=project_id,
                    story_bible=sb_raw,
                    visual_style_profile=visual_style_profile,
                )
            except Exception as e:
                xlog.warn(project_id, "[asset-mgr] portraits auto_demand failed project_id=%d err=%s", project_id, e)
                portrait_prompts = None

        if portrait_prompts is None:
            # fallback：旧 prompt builder 笛卡尔积路径（auto_demand 关 / LLM 失败）
            portrait_prompts = await prompt_builder_service.build_portrait_prompts_async(
                sb_raw,
                project_id,
                variations,
                visual_style_profile=visual_style_profile,
            )
            if auto_demand and PORTRAIT_AUTO_DEMAND_ENABLED:
                demand_source = "fallback"
            else:
                demand_source = "variations" if variations else "default"

        xlog.info(project_id, "[asset-mgr] portraits prompt built project_id=%d count=%d source=%s", project_id, len(portrait_prompts), demand_source)

        results = []
        failed = 0
        generated = 0

        from app.core.config import get_settings
        _parallel = get_settings().parallel_portrait_generation

        # 批量生成
        for i in range(0, len(portrait_prompts), batch_size):
            batch = portrait_prompts[i:i+batch_size]

            if _parallel:
                # === 两段式：阶段 A 并发（cache 查 + 供应商调用，独立只读 session）===
                from app.database import SessionLocal

                async def _resolve_portrait(prompt_data):
                    """阶段 A：返回 cache 命中信息或供应商结果。不碰 self.db 写。"""
                    # cache 查用独立只读 session
                    own_db = SessionLocal()
                    try:
                        candidates = own_db.query(Asset).filter(
                            Asset.project_id == project_id,
                            Asset.asset_type == "portrait",
                            Asset.character_id == prompt_data["character_id"],
                            Asset.emotion == prompt_data["emotion"],
                            Asset.outfit == prompt_data.get("outfit"),
                            Asset.pose == prompt_data.get("pose")
                        ).all()
                        existing, stale_existing = self._select_portrait_cache_candidate(
                            candidates,
                            prompt_data,
                        )
                        stale_existing_id = stale_existing.id if stale_existing is not None else None
                        if stale_existing is not None:
                            xlog.info(
                                project_id,
                                "[asset-mgr] portrait cache stale project_id=%d asset_id=%d character=%s reason=gender_or_prompt_contract",
                                project_id, stale_existing_id, prompt_data["character_name"],
                            )

                        if existing:
                            xlog.info(
                                project_id,
                                "[asset-mgr] portrait cache hit project_id=%d character=%s emotion=%s outfit=%s pose=%s asset_id=%d",
                                project_id, prompt_data["character_name"], prompt_data["emotion"],
                                prompt_data.get("outfit"), prompt_data.get("pose"), existing.id,
                            )
                            return {
                                "cached": True,
                                "asset_id": existing.id,
                                "generation_time": existing.generation_time,
                                "stale_existing_id": stale_existing_id,
                            }

                        # 调供应商（受 _image_semaphore 全局限流）
                        start_time = time.time()
                        result = await image_generation_service.generate_portrait(
                            character_id=prompt_data["character_id"],
                            character_name=prompt_data["character_name"],
                            appearance_prompt=prompt_data["appearance_prompt"],
                            emotion=prompt_data["emotion"],
                            outfit=prompt_data.get("outfit"),
                            pose=prompt_data.get("pose"),
                            genre=prompt_data.get("genre"),
                            seed=prompt_data["seed"],
                            remove_bg=True,
                            full_body=True,
                            final_prompt=prompt_data.get("final_prompt"),
                            final_prompt_source=prompt_data.get("final_prompt_source"),
                            gender=prompt_data.get("gender"),
                            gender_prompt=prompt_data.get("gender_prompt"),
                            age_contract=prompt_data.get("age_contract"),
                            visual_style_prompt=prompt_data.get("visual_style_prompt"),
                        )
                        generation_time = time.time() - start_time
                        return {
                            "cached": False,
                            "result": result,
                            "generation_time": generation_time,
                            "stale_existing_id": stale_existing_id,
                        }
                    finally:
                        own_db.close()

                batch_tasks = [_resolve_portrait(p) for p in batch]
                batch_outcomes = await asyncio.gather(*batch_tasks, return_exceptions=True)

                # === 阶段 B：串行持久化（主 session 单协程）===
                for prompt_data, outcome in zip(batch, batch_outcomes):
                    variation_info = {
                        "emotion": prompt_data["emotion"],
                        "outfit": prompt_data.get("outfit"),
                        "pose": prompt_data.get("pose"),
                        "gender": prompt_data.get("gender"),
                        "age_group": prompt_data.get("age_group"),
                        "identity_variant_id": prompt_data.get("identity_variant_id"),
                        "demand_source": demand_source,
                        "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                        "source_excerpt": prompt_data.get("source_excerpt"),
                        "rationale": prompt_data.get("rationale"),
                    }
                    variation_info = {k: v for k, v in variation_info.items() if v not in (None, "")}

                    # gather 异常 → 计失败
                    if isinstance(outcome, Exception):
                        failed += 1
                        xlog.error(project_id, outcome, "[asset-mgr] portrait exception project_id=%d character=%s", project_id, prompt_data.get("character_name", "unknown"))
                        results.append({
                            "success": False,
                            "character_name": prompt_data.get("character_name", "unknown"),
                            "emotion": prompt_data.get("emotion", "unknown"),
                            "outfit": prompt_data.get("outfit"),
                            "pose": prompt_data.get("pose"),
                            "gender": prompt_data.get("gender"),
                            "age_group": prompt_data.get("age_group"),
                            "demand_source": demand_source,
                            "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                            "error": str(outcome),
                            "generation_time": 0,
                        })
                        continue

                    # cache 命中
                    if outcome.get("cached"):
                        results.append({
                            "success": True,
                            "cached": True,
                            "asset_id": outcome["asset_id"],
                            "character_name": prompt_data["character_name"],
                            "emotion": prompt_data["emotion"],
                            "outfit": prompt_data.get("outfit"),
                            "pose": prompt_data.get("pose"),
                            "gender": prompt_data.get("gender"),
                            "age_group": prompt_data.get("age_group"),
                            "demand_source": demand_source,
                            "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                            "source_excerpt": prompt_data.get("source_excerpt"),
                            "rationale": prompt_data.get("rationale"),
                            "generation_time": outcome["generation_time"],
                        })
                        continue

                    # 供应商返回（成功或失败）
                    result = outcome["result"]
                    generation_time = outcome["generation_time"]
                    stale_existing_id = outcome.get("stale_existing_id")

                    stat = self.stat_service.start_generation(
                        project_id=project_id,
                        generation_type="portrait",
                        target_name=prompt_data["character_name"],
                        variation_info=variation_info,
                    )

                    if result["success"]:
                        self.stat_service.end_generation(stat.id, success=True)
                        # 主 session 重新查 stale_existing（跨 session 对象失效）
                        stale_existing = (
                            self.db.query(Asset).filter(Asset.id == stale_existing_id).first()
                            if stale_existing_id else None
                        )
                        asset = stale_existing or Asset(
                            project_id=project_id,
                            asset_type="portrait",
                            character_id=prompt_data["character_id"],
                        )
                        asset.target_name = prompt_data["character_name"]
                        asset.prompt = result.get("prompt") or prompt_data.get("final_prompt") or prompt_data["appearance_prompt"]
                        asset.image_url = result["image_url"]
                        asset.status = "completed"
                        asset.emotion = prompt_data["emotion"]
                        asset.outfit = prompt_data.get("outfit")
                        asset.pose = prompt_data.get("pose")
                        asset.seed = prompt_data["seed"]
                        # A05/A06 — 身份契约 + 身份参考三件套写入立绘 generation_params，
                        # 让 identity_master_resolver 能选出母版、关键帧阶段能拿到 reference_image_url。
                        from app.core.config import get_settings as _get_settings
                        _portrait_settings = _get_settings()
                        _portrait_params_base = {
                            **dict(result.get("generation_params") or {}),
                            **variation_info,
                            "appearance_prompt": prompt_data.get("appearance_prompt"),
                            "character_visual_profile": prompt_data.get("character_visual_profile"),
                            "visual_description_cn": prompt_data.get("visual_description_cn"),
                            "visual_fingerprint": prompt_data.get("visual_fingerprint"),
                            "canonical_identity": prompt_data.get("canonical_identity"),
                            "canonical_identity_fingerprint": prompt_data.get("canonical_identity_fingerprint"),
                            "character_visual_anchor": prompt_data.get("character_visual_anchor"),
                            "project_genre": prompt_data.get("project_genre"),
                            "style_fingerprint": prompt_data.get("style_fingerprint"),
                            "visual_style_profile": prompt_data.get("visual_style_profile"),
                            "visual_style_prompt": prompt_data.get("visual_style_prompt"),
                            "final_prompt": result.get("prompt") or prompt_data.get("final_prompt"),
                            # 身份契约字段（A05）
                            "identity_contract_version": _portrait_settings.character_identity_contract_version,
                            # 默认立绘不是身份母版；只有 identity_master_resolver 通过
                            # _generate_master_portrait 写入的行才会标 True。
                            "is_identity_master": bool(prompt_data.get("is_identity_master") or False),
                            # A06 — 身份参考三件套（result 来自 image_generation_service，
                            # 已写好这三条；这里转存到 Asset.generation_params）
                            "source_image_url": result.get("source_image_url"),
                            "identity_reference_url": result.get("identity_reference_url"),
                            "presentation_url": result.get("presentation_url") or result.get("image_url"),
                            "reference_image_sha256": result.get("source_image_sha256"),
                            "gender": prompt_data.get("gender"),
                            "gender_prompt": prompt_data.get("gender_prompt"),
                            "identity_variant_id": prompt_data.get("identity_variant_id"),
                            "age_group": prompt_data.get("age_group"),
                            "age_contract": prompt_data.get("age_contract"),
                            "age_validation": result.get("age_validation"),
                            "age_validation_attempts": result.get("age_validation_attempts") or [],
                        }
                        asset.generation_params = self._stamp_visual_bible_params(
                            _portrait_params_base,
                            bible,
                            scene_selector=prompt_data.get("character_name"),
                            project_id=project_id,
                        )
                        asset.genre = prompt_data.get("genre")
                        asset.description_cn = (
                            prompt_data.get("visual_description_cn")
                            or prompt_data.get("source_excerpt")
                        )
                        asset.generation_time = generation_time
                        asset.processed = 1
                        if stale_existing is None:
                            self.db.add(asset)
                        self.db.commit()
                        generated += 1
                        xlog.info(project_id, "[asset-mgr] portrait generated project_id=%d asset_id=%d character=%s elapsed_ms=%d", project_id, asset.id, prompt_data["character_name"], int(generation_time * 1000))

                        # project-visual-bible-v2 §B6 — normalize into a
                        # canonical presentation canvas and persist the URL
                        # via decision 3 (Asset.generation_params, zero DB
                        # migration). Advisory-only — failure does not
                        # invalidate the portrait.
                        try:
                            from pathlib import Path as _P
                            img_url = result.get("image_url") or ""
                            local_path = _P(str(img_url).lstrip("/")) if img_url else None
                            if local_path and local_path.is_file():
                                default_normalizer().normalize_for_asset(
                                    asset_id=asset.id,
                                    image_path=local_path,
                                    image_url=img_url,
                                    db=self.db,
                                )
                        except Exception as _norm_err:
                            xlog.info(
                                project_id,
                                "[asset-mgr] portrait normalize skipped asset_id=%d err=%s",
                                asset.id, _norm_err,
                            )
                        results.append({
                            "success": True,
                            "asset_id": asset.id,
                            "character_name": prompt_data["character_name"],
                            "emotion": prompt_data["emotion"],
                            "outfit": prompt_data.get("outfit"),
                            "pose": prompt_data.get("pose"),
                            "gender": prompt_data.get("gender"),
                            "age_group": prompt_data.get("age_group"),
                            "demand_source": demand_source,
                            "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                            "source_excerpt": prompt_data.get("source_excerpt"),
                            "rationale": prompt_data.get("rationale"),
                            "image_url": result["image_url"],
                            "generation_time": generation_time,
                        })
                    else:
                        self.stat_service.end_generation(stat.id, success=False, error_message=result.get("error"))
                        failed += 1
                        xlog.warn(project_id, "[asset-mgr] portrait failed project_id=%d character=%s error=%s", project_id, prompt_data["character_name"], result.get("error"))
                        results.append({
                            "success": False,
                            "character_name": prompt_data["character_name"],
                            "emotion": prompt_data["emotion"],
                            "outfit": prompt_data.get("outfit"),
                            "pose": prompt_data.get("pose"),
                            "gender": prompt_data.get("gender"),
                            "demand_source": demand_source,
                            "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                            "error": result.get("error", "生成失败"),
                            "generation_time": generation_time,
                        })
                continue  # 跳过下方串行路径

            # === 串行路径（原行为，默认）===
            for prompt_data in batch:
                stat = None
                try:
                    # 检查是否已存在（避免重复生成）
                    candidates = self.db.query(Asset).filter(
                        Asset.project_id == project_id,
                        Asset.asset_type == "portrait",
                        Asset.character_id == prompt_data["character_id"],
                        Asset.emotion == prompt_data["emotion"],
                        Asset.outfit == prompt_data.get("outfit"),
                        Asset.pose == prompt_data.get("pose")
                    ).all()
                    existing, stale_existing = self._select_portrait_cache_candidate(
                        candidates,
                        prompt_data,
                    )
                    if stale_existing is not None:
                        xlog.info(
                            project_id,
                            "[asset-mgr] portrait cache stale project_id=%d asset_id=%d character=%s reason=gender_or_prompt_contract",
                            project_id, stale_existing.id, prompt_data["character_name"],
                        )

                    if existing:
                        xlog.info(
                            project_id,
                            "[asset-mgr] portrait cache hit project_id=%d character=%s emotion=%s outfit=%s pose=%s asset_id=%d",
                            project_id, prompt_data["character_name"], prompt_data["emotion"],
                            prompt_data.get("outfit"), prompt_data.get("pose"), existing.id,
                        )
                        results.append({
                            "success": True,
                            "cached": True,
                            "asset_id": existing.id,
                            "character_name": prompt_data["character_name"],
                            "emotion": prompt_data["emotion"],
                            "outfit": prompt_data.get("outfit"),
                            "pose": prompt_data.get("pose"),
                            "gender": prompt_data.get("gender"),
                            "age_group": prompt_data.get("age_group"),
                            "demand_source": demand_source,
                            "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                            "source_excerpt": prompt_data.get("source_excerpt"),
                            "rationale": prompt_data.get("rationale"),
                            "generation_time": existing.generation_time
                        })
                        continue

                    # 开始记录生成时间
                    variation_info = {
                        "emotion": prompt_data["emotion"],
                        "outfit": prompt_data.get("outfit"),
                        "pose": prompt_data.get("pose"),
                        "gender": prompt_data.get("gender"),
                        "age_group": prompt_data.get("age_group"),
                        "identity_variant_id": prompt_data.get("identity_variant_id"),
                        "demand_source": demand_source,
                        "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                        "source_excerpt": prompt_data.get("source_excerpt"),
                        "rationale": prompt_data.get("rationale"),
                    }
                    variation_info = {k: v for k, v in variation_info.items() if v not in (None, "")}

                    stat = self.stat_service.start_generation(
                        project_id=project_id,
                        generation_type="portrait",
                        target_name=prompt_data["character_name"],
                        variation_info=variation_info
                    )

                    start_time = time.time()

                    # 调用图像生成服务
                    result = await image_generation_service.generate_portrait(
                        character_id=prompt_data["character_id"],
                        character_name=prompt_data["character_name"],
                        appearance_prompt=prompt_data["appearance_prompt"],
                        emotion=prompt_data["emotion"],
                        outfit=prompt_data.get("outfit"),
                        pose=prompt_data.get("pose"),
                        genre=prompt_data.get("genre"),
                        seed=prompt_data["seed"],
                        remove_bg=True,
                        full_body=True,
                        final_prompt=prompt_data.get("final_prompt"),
                        final_prompt_source=prompt_data.get("final_prompt_source"),
                        gender=prompt_data.get("gender"),
                        gender_prompt=prompt_data.get("gender_prompt"),
                        age_contract=prompt_data.get("age_contract"),
                        visual_style_prompt=prompt_data.get("visual_style_prompt"),
                    )

                    generation_time = time.time() - start_time

                    if result["success"]:
                        # 结束记录生成时间
                        self.stat_service.end_generation(stat.id, success=True)

                        # 保存到数据库
                        asset = stale_existing or Asset(
                            project_id=project_id,
                            asset_type="portrait",
                            character_id=prompt_data["character_id"],
                        )
                        asset.target_name = prompt_data["character_name"]
                        asset.prompt = result.get("prompt") or prompt_data.get("final_prompt") or prompt_data["appearance_prompt"]
                        asset.image_url = result["image_url"]
                        asset.status = "completed"
                        asset.emotion = prompt_data["emotion"]
                        asset.outfit = prompt_data.get("outfit")
                        asset.pose = prompt_data.get("pose")
                        asset.seed = prompt_data["seed"]
                        # A05/A06 — 身份契约 + 身份参考三件套写入立绘 generation_params，
                        # 让 identity_master_resolver 能选出母版、关键帧阶段能拿到 reference_image_url。
                        from app.core.config import get_settings as _get_settings
                        _portrait_settings = _get_settings()
                        _portrait_params_base = {
                            **dict(result.get("generation_params") or {}),
                            **variation_info,
                            "appearance_prompt": prompt_data.get("appearance_prompt"),
                            "character_visual_profile": prompt_data.get("character_visual_profile"),
                            "visual_description_cn": prompt_data.get("visual_description_cn"),
                            "visual_fingerprint": prompt_data.get("visual_fingerprint"),
                            "canonical_identity": prompt_data.get("canonical_identity"),
                            "canonical_identity_fingerprint": prompt_data.get("canonical_identity_fingerprint"),
                            "character_visual_anchor": prompt_data.get("character_visual_anchor"),
                            "project_genre": prompt_data.get("project_genre"),
                            "style_fingerprint": prompt_data.get("style_fingerprint"),
                            "visual_style_profile": prompt_data.get("visual_style_profile"),
                            "visual_style_prompt": prompt_data.get("visual_style_prompt"),
                            "final_prompt": result.get("prompt") or prompt_data.get("final_prompt"),
                            # 身份契约字段（A05）
                            "identity_contract_version": _portrait_settings.character_identity_contract_version,
                            # 默认立绘不是身份母版；只有 identity_master_resolver 通过
                            # _generate_master_portrait 写入的行才会标 True。
                            "is_identity_master": bool(prompt_data.get("is_identity_master") or False),
                            # A06 — 身份参考三件套（result 来自 image_generation_service，
                            # 已写好这三条；这里转存到 Asset.generation_params）
                            "source_image_url": result.get("source_image_url"),
                            "identity_reference_url": result.get("identity_reference_url"),
                            "presentation_url": result.get("presentation_url") or result.get("image_url"),
                            "reference_image_sha256": result.get("source_image_sha256"),
                            "gender": prompt_data.get("gender"),
                            "gender_prompt": prompt_data.get("gender_prompt"),
                            "identity_variant_id": prompt_data.get("identity_variant_id"),
                            "age_group": prompt_data.get("age_group"),
                            "age_contract": prompt_data.get("age_contract"),
                            "age_validation": result.get("age_validation"),
                            "age_validation_attempts": result.get("age_validation_attempts") or [],
                        }
                        asset.generation_params = self._stamp_visual_bible_params(
                            _portrait_params_base,
                            bible,
                            scene_selector=prompt_data.get("character_name"),
                            project_id=project_id,
                        )
                        asset.genre = prompt_data.get("genre")
                        asset.description_cn = (
                            prompt_data.get("visual_description_cn")
                            or prompt_data.get("source_excerpt")
                        )
                        asset.generation_time = generation_time
                        asset.processed = 1
                        if stale_existing is None:
                            self.db.add(asset)
                        self.db.commit()
                        generated += 1
                        xlog.info(project_id, "[asset-mgr] portrait generated project_id=%d asset_id=%d character=%s elapsed_ms=%d", project_id, asset.id, prompt_data["character_name"], int(generation_time * 1000))

                        # project-visual-bible-v2 §B6 — normalize into a
                        # canonical presentation canvas and persist the URL
                        # via decision 3 (Asset.generation_params, zero DB
                        # migration). Advisory-only — failure does not
                        # invalidate the portrait.
                        try:
                            from pathlib import Path as _P
                            img_url = result.get("image_url") or ""
                            local_path = _P(str(img_url).lstrip("/")) if img_url else None
                            if local_path and local_path.is_file():
                                default_normalizer().normalize_for_asset(
                                    asset_id=asset.id,
                                    image_path=local_path,
                                    image_url=img_url,
                                    db=self.db,
                                )
                        except Exception as _norm_err:
                            xlog.info(
                                project_id,
                                "[asset-mgr] portrait normalize skipped asset_id=%d err=%s",
                                asset.id, _norm_err,
                            )
                        results.append({
                            "success": True,
                            "asset_id": asset.id,
                            "character_name": prompt_data["character_name"],
                            "emotion": prompt_data["emotion"],
                            "outfit": prompt_data.get("outfit"),
                            "pose": prompt_data.get("pose"),
                            "gender": prompt_data.get("gender"),
                            "age_group": prompt_data.get("age_group"),
                            "demand_source": demand_source,
                            "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                            "source_excerpt": prompt_data.get("source_excerpt"),
                            "rationale": prompt_data.get("rationale"),
                            "image_url": result["image_url"],
                            "generation_time": generation_time
                        })
                    else:
                        # 记录失败
                        self.stat_service.end_generation(stat.id, success=False, error_message=result.get("error"))
                        failed += 1
                        xlog.warn(project_id, "[asset-mgr] portrait failed project_id=%d character=%s error=%s", project_id, prompt_data["character_name"], result.get("error"))
                        results.append({
                            "success": False,
                            "character_name": prompt_data["character_name"],
                            "emotion": prompt_data["emotion"],
                            "outfit": prompt_data.get("outfit"),
                            "pose": prompt_data.get("pose"),
                            "gender": prompt_data.get("gender"),
                            "demand_source": demand_source,
                            "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                            "error": result.get("error", "生成失败"),
                            "generation_time": generation_time
                        })

                except Exception as e:
                    # 记录失败
                    if stat:
                        self.stat_service.end_generation(stat.id, success=False, error_message=str(e))
                    failed += 1
                    xlog.error(project_id, e, "[asset-mgr] portrait exception project_id=%d character=%s", project_id, prompt_data.get("character_name", "unknown"))
                    results.append({
                        "success": False,
                        "character_name": prompt_data.get("character_name", "unknown"),
                        "emotion": prompt_data.get("emotion", "unknown"),
                        "outfit": prompt_data.get("outfit"),
                        "pose": prompt_data.get("pose"),
                        "gender": prompt_data.get("gender"),
                        "demand_source": demand_source,
                        "source_chapter": prompt_data.get("auto_demand_source_chapter"),
                        "error": str(e),
                        "generation_time": 0  # 异常时设为 0，避免 None
                    })

        # 计算 total_time，过滤掉 None 值
        total_time = sum(r.get("generation_time") or 0 for r in results)
        valid_times = [r.get("generation_time") for r in results if r.get("generation_time")]
        xlog.info(project_id, "[asset-mgr] portraits done project_id=%d total=%d generated=%d failed=%d", project_id, len(portrait_prompts), generated, failed)

        return {
            "total": len(portrait_prompts),
            "generated": generated,
            "failed": failed,
            "demand_source": demand_source,
            "total_time": total_time,
            "avg_time": sum(valid_times) / generated if generated > 0 else 0,
            "results": results
        }

    async def generate_chapter_backgrounds(
        self,
        project_id: int,
        chapter_index: int,
        moods: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """生成章节背景图 — 统一转发到 segment-权威的 v2 接口。

        历史「旧路径」按 segmenter 切段后走 prompt_builder；「v2」原先由
        analyzer 自行决定张数。现已合并：对外只保留本方法与
        ``generate_chapter_backgrounds_v2``（二者等价），以 VNGraph 同源的
        ``SceneSegmenterService`` 定张数与 segment_id，analyzer 仅做 enrich。
        """
        return await self.generate_chapter_backgrounds_v2(
            project_id=project_id,
            chapter_index=chapter_index,
            moods=moods,
        )

    async def _build_auto_demand_portrait_prompts(
        self,
        project_id: int,
        story_bible: Dict[str, Any],
        visual_style_profile: Optional[VisualStyleProfile] = None,
    ) -> tuple[Optional[List[Dict[str, Any]]], str]:
        """P1.3 — auto_demand 路径专用：用 ``PortraitDemandAnalyzer`` 从章节正文
        抽 per-character 的 (emotion, outfit, pose) 变体，再按 character 外貌 +
        rewriter 拼出与 ``build_portrait_prompts_async`` 同 schema 的 prompt 列表。

        与旧 ``build_portrait_prompts`` 笛卡尔积路径的关键差别：每条 demand 已经
        绑定到具体 ``character_id``，不再对全部角色 × 全部变体展开。

        Returns:
            (prompts, source) — prompts 为 None 时 caller 应自行 fallback。
            source ∈ {"auto_demand", "fallback_empty"}。
        """
        from app.services.portrait_demand_analyzer import PortraitDemandAnalyzer

        outlines = self.db.query(ChapterOutline).filter(
            ChapterOutline.project_id == project_id
        ).all()
        chapter_indices = sorted({
            o.chapter_index for o in outlines
            if getattr(o, "chapter_index", None) is not None
        })

        analyzer = PortraitDemandAnalyzer(self.db)
        demands = await analyzer.analyze(project_id, chapter_indices, story_bible)
        if not demands:
            xlog.info(project_id, "[asset-mgr] auto_demand no demands project_id=%d (will fallback)", project_id)
            return None, "fallback_empty"
        style_profile = visual_style_profile or self._build_visual_style_profile(project_id, story_bible)

        # 按 character_name 索引 story_bible 角色卡，拿外观 / 题材
        characters_by_name: Dict[str, Dict[str, Any]] = {}
        for c in story_bible.get("characters", []) or []:
            if isinstance(c, str):
                c = {"name": c}
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or c.get("name_cn") or "").strip()
            if name:
                characters_by_name[name] = c

        prompts: List[Dict[str, Any]] = []
        for d in demands:
            character_context = "\n".join(
                part
                for part in (
                    str(story_bible.get("worldview") or "").strip(),
                    str(d.source_excerpt or "").strip(),
                    str(d.rationale or "").strip(),
                )
                if part
            )
            char = normalize_character(
                characters_by_name.get(d.character_name, {"name": d.character_name}),
                # Newly introduced characters may not have a StoryBible card
                # yet. Their exact source excerpt gives the generic profile
                # service enough evidence to derive one stable identity while
                # the project-level visual profile keeps the art style locked.
                context_text=character_context,
            )
            age_contract = build_age_contract(
                d.age_group,
                fallback_age_group=str((char.get("visual_profile") or {}).get("age_group") or "young_adult"),
                age_source=d.age_source,
                source_excerpt=d.source_excerpt,
            )
            char = apply_age_contract_to_character(char, age_contract)
            appearance = source_visual_profile_service.effective_character_appearance(
                char,
                style_profile.source_profile_id,
                prompt_builder_service._extract_appearance(char),
                source_work=str(story_bible.get("source_work") or ""),
                worldview=str(story_bible.get("worldview") or ""),
                style_rules=str(story_bible.get("style_rules") or ""),
            )
            appearance = apply_age_contract_to_appearance(appearance, age_contract)
            gender = prompt_builder_service._extract_gender(char)
            gender_prompt = prompt_builder_service.build_gender_prompt(gender)
            genre = style_profile.project_genre or prompt_builder_service._extract_genre_from_character(char, story_bible)
            character_anchor = visual_style_profile_service.character_visual_anchor(
                d.character_name,
                appearance,
                gender,
            )
            variant_id = identity_variant_id(d.character_id, age_contract.age_group)
            seed = prompt_builder_service.generate_seed(
                variant_id,
                d.emotion,
                style_fingerprint=style_profile.fingerprint,
            )
            prompts.append({
                "character_id": d.character_id,
                "character_name": d.character_name,
                "appearance_prompt": appearance,
                "character_visual_profile": char.get("visual_profile"),
                "visual_description_cn": char.get("visual_description_cn"),
                "visual_fingerprint": char.get("visual_fingerprint"),
                "canonical_identity": char.get("canonical_identity"),
                "canonical_identity_fingerprint": char.get("canonical_identity_fingerprint"),
                "character_visual_anchor": character_anchor,
                "identity_variant_id": variant_id,
                "age_group": age_contract.age_group,
                "age_contract": age_contract.to_dict(),
                "gender": gender,
                "gender_prompt": gender_prompt,
                "emotion": d.emotion,
                "outfit": d.outfit,
                "pose": d.pose,
                "genre": genre,
                "project_genre": style_profile.project_genre,
                "visual_style_profile": style_profile.to_dict(),
                "visual_style_prompt": style_profile.portrait_prompt_en,
                "style_fingerprint": style_profile.fingerprint,
                "seed": seed,
                "auto_demand_source_chapter": d.source_chapter_index,
                "source_excerpt": d.source_excerpt,
                "rationale": d.rationale,
            })

        # 走与 build_portrait_prompts_async 一致的 rewriter 路径
        from app.services.prompt_rewriter_service import prompt_rewriter_service
        batch_fields = []
        for p in prompts:
            identity_fields = build_portrait_rewriter_identity(p)
            batch_fields.append({
                "asset_type": "portrait",
                "character_id": p["character_id"],
                **identity_fields,
                "age_group": p.get("age_group"),
                "age_contract": p.get("age_contract"),
                "gender": p.get("gender"),
                "gender_prompt": p.get("gender_prompt"),
                "emotion": p["emotion"],
                "outfit": p.get("outfit"),
                "pose": p.get("pose"),
                "genre": p.get("genre"),
                "project_genre": p.get("project_genre"),
                "visual_style_profile": p.get("visual_style_profile"),
                "visual_style_prompt": p.get("visual_style_prompt"),
                "style_fingerprint": p.get("style_fingerprint"),
                "shot": "full_body",
            })
        rewritten = await prompt_rewriter_service.rewrite_many("portrait", batch_fields)
        for p, r in zip(prompts, rewritten):
            if r is not None:
                p["final_prompt"] = r.to_cogview_prompt()
                p["final_prompt_source"] = "llm_rewriter"
            else:
                p["prompt_rewrite_error"] = "portrait_llm_rewrite_failed"

        xlog.info(
            project_id,
            "[asset-mgr] auto_demand built %d prompts project_id=%d",
            len(prompts), project_id,
        )
        return prompts, "auto_demand"

    async def _build_keyframe_prompts_from_moments(
        self,
        moments: List[Any],
        outline_json: Dict[str, Any],
        story_bible: Dict[str, Any],
        visual_style_profile: Optional[VisualStyleProfile] = None,
        *,
        contracts: Optional[Dict[str, "CharacterIdentityContract"]] = None,
    ) -> List[Dict[str, Any]]:
        """P1.3 — 把 ``KeyframeMomentSelector`` 输出的 ``KeyframeMoment`` 列表转成
        与 ``build_keyframe_prompts`` 同 schema 的 prompt，并走 rewriter 出 final_prompt。

        B04 — 每个 moment 的 ``target_characters`` 现在会构造完整
        ``KeyframeCharacterBinding``（含 reference 字段），不再退化为
        ``{name, appearance}``。``contracts`` 由上游
        ``generate_chapter_keyframes`` 在调用前先解析身份母版得到；未传入时
        退化 binding 仍带 visual_fingerprint / identity_prompt，但 reference
        字段为空（``has_identity_reference=False``），下游会拒绝生成。
        """
        from app.services.prompt_rewriter_service import prompt_rewriter_service
        from app.services.keyframe_character_binding import (
            KeyframeCharacterBinding,
            binding_from_contract,
        )

        style_profile = visual_style_profile or visual_style_profile_service.build_profile(story_bible)
        characters_index: Dict[str, Dict[str, Any]] = {}
        for c in story_bible.get("characters", []) or []:
            name = (c.get("name") or c.get("name_cn") or "").strip()
            if name:
                characters_index[name] = c
        project_id = int(story_bible.get("project_id") or 0)
        contracts = contracts or {}

        prompts: List[Dict[str, Any]] = []
        for m in moments:
            target_names = list(getattr(m, "target_characters", []) or [])
            scene = (getattr(m, "visual_focus", "") or "").strip()
            scene = scene or outline_json.get("scene", "") or outline_json.get("summary", "")
            event_name = (getattr(m, "moment_summary", "") or "").strip() or outline_json.get("title", "") or "关键场景"

            # B04 — 构造完整 KeyframeCharacterBinding
            bindings: List[KeyframeCharacterBinding] = []
            for n in target_names[:3]:
                card = characters_index.get(n) or prompt_builder_service._find_character_record(n, story_bible)
                char_id = prompt_builder_service.generate_character_id(n, project_id)
                # E05 — 从 moment 里抓 per-character 服装/动作/情绪/伤情/物品
                mom_emotion = getattr(m, "get_emotion", None)
                mom_outfit = getattr(m, "get_outfit", None)
                mom_pose = getattr(m, "get_pose", None)
                mom_action = getattr(m, "get_action", None)
                mom_injury = getattr(m, "get_injury_state", None)
                mom_held = getattr(m, "get_held_item", None)
                mom_age = getattr(m, "get_age_group", None)
                e_state = mom_emotion(n) if callable(mom_emotion) else ""
                o_state = mom_outfit(n) if callable(mom_outfit) else ""
                p_state = mom_pose(n) if callable(mom_pose) else ""
                a_state = mom_action(n) if callable(mom_action) else ""
                i_state = mom_injury(n) if callable(mom_injury) else ""
                h_state = mom_held(n) if callable(mom_held) else ""
                requested_age = mom_age(n) if callable(mom_age) else ""
                profile = (card or {}).get("visual_profile") or {}
                age_contract = age_contract_from_profile(
                    profile,
                    override=requested_age,
                    age_source="keyframe_moment",
                    source_excerpt=str(getattr(m, "source_excerpt", "") or ""),
                )
                variant_id = identity_variant_id(char_id, age_contract.age_group)
                contract = (
                    contracts.get(variant_id)
                    or contracts.get(char_id)
                    or contracts.get(n)
                )
                if contract is not None and contract.age_group != age_contract.age_group:
                    contract = None
                if contract is not None:
                    bindings.append(binding_from_contract(
                        contract,
                        requested_emotion=e_state or "neutral",
                        requested_outfit=o_state or "default",
                        requested_pose=p_state or "standing",
                        requested_action=a_state,
                        injury_state=i_state or "none",
                        held_item=h_state,
                    ))
                    continue
                # 退化路径：没有 contract 时仍然带 visual_fingerprint / identity_prompt
                variant_card = apply_age_contract_to_character(dict(card or {"name": n}), age_contract)
                profile = variant_card.get("visual_profile") or {}
                fingerprint = str(variant_card.get("visual_fingerprint") or "")
                identity_prompt = canonical_identity_prompt(variant_card) or str(
                    variant_card.get("visual_prompt_en") or ""
                )
                gender = str(variant_card.get("gender") or "")
                from app.services.image_generation_service import ImageGenerationService
                gender_prompt = ImageGenerationService()._build_portrait_gender_anchor(gender)
                anchor = visual_style_profile_service.character_visual_anchor(
                    n,
                    identity_prompt or str((card or {}).get("appearance") or ""),
                    gender,
                )
                bindings.append(KeyframeCharacterBinding(
                    character_id=char_id,
                    character_name=n,
                    visual_fingerprint=fingerprint,
                    identity_contract_version="character-identity-v2",
                    identity_prompt=identity_prompt,
                    gender_prompt=gender_prompt,
                    identity_variant_id=variant_id,
                    age_group=age_contract.age_group,
                    age_contract=age_contract.to_dict(),
                    visual_profile=dict(profile) if profile else {},
                    identity_anchor=anchor,
                    canonical_outfit=dict(profile.get("outfit") or {}) if isinstance(profile.get("outfit"), dict) else {},
                    signature_features=tuple(profile.get("signature_features") or []),
                    accessories=tuple(profile.get("accessories") or []),
                    canonical_identity=(
                        dict(variant_card.get("canonical_identity"))
                        if isinstance(variant_card.get("canonical_identity"), dict)
                        else {}
                    ),
                    canonical_identity_fingerprint=str(
                        variant_card.get("canonical_identity_fingerprint") or ""
                    ),
                    requested_emotion=e_state or "neutral",
                    requested_outfit=o_state or "default",
                    requested_pose=p_state or "standing",
                    requested_action=a_state,
                    injury_state=i_state or "none",
                    held_item=h_state,
                ))

            # 兼容 dict：保留 name/appearance 给旧 rewriter，同时暴露完整 binding
            chars_for_legacy = [
                {
                    "name": b.character_name,
                    "appearance": b.identity_prompt or b.identity_anchor,
                    "character_id": b.character_id,
                    "visual_fingerprint": b.visual_fingerprint,
                    "identity_variant_id": b.identity_variant_id,
                    "age_group": b.age_group,
                    "age_contract": b.age_contract,
                    "has_identity_reference": b.has_reference(),
                    "binding": b,
                }
                for b in bindings
            ]

            emotion = outline_json.get("emotion", "neutral")
            genre = style_profile.project_genre
            identity_lock = prompt_builder_service.build_character_identity_lock(bindings)

            prompts.append({
                "event_name": event_name,
                "scene_description": scene,
                "characters": chars_for_legacy,
                "character_bindings": bindings,
                "character_identity_lock": identity_lock,
                "action": prompt_builder_service._extract_action_from_conflict(
                    outline_json.get("conflict", "") or outline_json.get("summary", "")
                ),
                "emotion": prompt_builder_service._map_emotion_to_keyframe(emotion),
                "genre": genre,
                "project_genre": style_profile.project_genre,
                "visual_style_profile": style_profile.to_dict(),
                "visual_style_prompt": style_profile.keyframe_prompt_en,
                "style_fingerprint": style_profile.fingerprint,
                "_keyframe_moment_rationale": getattr(m, "rationale", ""),
                "_keyframe_moment_excerpt": getattr(m, "source_excerpt", ""),
                # Stage_VNGraph_Assembly_AR — moment 额外字段，供 keyframe 持久化
                # 时落 anchor + 设 asset.prompt。target_character_ids 用真实
                # project_id 算 cid（与 portrait 侧算法一致）。
                "_keyframe_moment_summary": getattr(m, "moment_summary", ""),
                "_keyframe_visual_focus": getattr(m, "visual_focus", ""),
                "_keyframe_target_character_ids": [
                    prompt_builder_service.generate_character_id(n, project_id)
                    for n in (target_names or [])
                ],
            })

        batch_fields = []
        for p in prompts:
            chars_for_llm = []
            for c in p.get("characters", []):
                binding = c.get("binding")
                if binding is not None:
                    chars_for_llm.append(binding.to_rewriter_payload())
                else:
                    chars_for_llm.append({
                        "name": c.get("name", ""),
                        "role": "aggressor" if "fighting" in p.get("action", "") else "bystander",
                        "appearance_zh": c.get("appearance", ""),
                    })
            batch_fields.append({
                "asset_type": "keyframe",
                "event_zh": p.get("event_name", ""),
                "scene_zh": p.get("scene_description", ""),
                "conflict_zh": outline_json.get("conflict", ""),
                "summary_zh": outline_json.get("summary", ""),
                "characters": chars_for_llm[:3],
                "character_identity_lock": p.get("character_identity_lock", ""),
                "emotion": p.get("emotion"),
                "genre": p.get("genre"),
                "project_genre": p.get("project_genre"),
                "visual_style_profile": p.get("visual_style_profile"),
                "visual_style_prompt": p.get("visual_style_prompt"),
                "style_fingerprint": p.get("style_fingerprint"),
            })
        rewritten = await prompt_rewriter_service.rewrite_many("keyframe", batch_fields)
        for p, r in zip(prompts, rewritten):
            if r is not None:
                p["final_prompt"] = r.to_cogview_prompt(
                    locked_style=str(p.get("visual_style_prompt") or ""),
                    use_final_prompt=False,
                )
        return prompts

    async def _generate_keyframe_with_identity_pipeline(
        self,
        *,
        project_id: int,
        chapter_index: int,
        prompt_data: Dict[str, Any],
        bible: Any = None,
        style_fingerprint: str = "",
        background_image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """C06 + D05/D07/D08 — 统一关键帧生成 wrapper（参考图 + 验收 + 重试 + 兜底）。

        流程：
        1. ``generate_keyframe_with_references`` 多图参考生成；
        2. ``KeyframeIdentityValidator`` 9 维身份验收；
        3. 失败 → 针对性 retry prompt + 确定性 retry seed，最多
           ``KEYFRAME_IDENTITY_MAX_RETRIES`` 次；
        4. 仍失败 → ``sprite_composite`` 兜底（已验收背景 + 已验收立绘）；
        5. 若 sprite 也失败且 ``KEYFRAME_ALLOW_TEXT_ONLY_FALLBACK=true`` →
           纯文字降级（``render_mode=text_only_degraded``，默认关）。

        所有失败路径都返回 ``success=False`` 或 ``render_mode=...degraded``，
        让上层 (D07) 拒绝把错脸图保存为 ``status=completed``。
        """
        from app.services.keyframe_character_binding import (
            KeyframeCharacterBinding,
            keyframe_identity_seed,
        )
        from app.services.keyframe_identity_validator_service import (
            KeyframeIdentityValidator,
        )
        from app.services.keyframe_scene_validator_service import (
            KeyframeSceneValidatorService,
            VERSION as SCENE_ANCHOR_CONTRACT_VERSION,
            augment_prompt_with_scene_contract,
            extract_required_scene_elements,
        )
        from app.services.keyframe_sprite_composer import (
            SpritePlacement,
            composite_keyframe,
        )
        from app.core.config import get_settings as _get_kf_settings

        settings = _get_kf_settings()
        bindings = prompt_data.get("character_bindings") or []
        bindings_typed = [
            b for b in bindings if isinstance(b, KeyframeCharacterBinding)
        ]
        bindings_with_ref = [b for b in bindings_typed if b.has_reference()]

        # E02 — missing identity reference 不得静默：
        # 有 binding 但没有可用 reference（既无 reference_asset_id 又无 url/sha）
        # 必须显式 log，否则下游关键帧只能跑 text_only_no_refs 路径，却无人察觉。
        bindings_without_ref = [b for b in bindings_typed if not b.has_reference()]
        if bindings_without_ref:
            missing_names = ", ".join(
                f"{b.character_name}({b.character_id})" for b in bindings_without_ref
            )
            xlog.warn(
                project_id,
                "[asset-mgr] keyframe identity reference missing project_id=%d chapter_index=%d "
                "missing=%d/%d names=[%s] — falling back to text-only-no-refs",
                project_id,
                chapter_index,
                len(bindings_without_ref),
                len(bindings_typed),
                missing_names,
            )

        vfp_list = sorted(
            b.canonical_identity_fingerprint or b.visual_fingerprint
            for b in bindings_typed
        )
        base_seed = keyframe_identity_seed(
            project_id=project_id,
            chapter_index=chapter_index,
            event_name=prompt_data.get("event_name", ""),
            style_fingerprint=style_fingerprint or prompt_data.get("style_fingerprint", ""),
            character_visual_fingerprints=vfp_list,
        )

        validator = KeyframeIdentityValidator()
        scene_validator = KeyframeSceneValidatorService()
        required_scene_elements = extract_required_scene_elements(prompt_data)
        configured_retries = max(0, int(settings.keyframe_identity_max_retries))
        max_retries = min(
            configured_retries,
            KEYFRAME_IDENTITY_PAID_RETRY_HARD_LIMIT,
        )
        if configured_retries > max_retries:
            xlog.warn(
                project_id,
                "[asset-mgr] keyframe identity retries capped configured=%d hard_limit=%d",
                configured_retries,
                KEYFRAME_IDENTITY_PAID_RETRY_HARD_LIMIT,
            )

        # --- Stage 1: 参考图条件生成 + 验收 + 重试 ---
        if bindings_with_ref:
            last_validation = None
            last_scene_validation = None
            attempt_count = max(1, max_retries + 1)
            paid_generation_attempts = 0
            xlog.info(
                project_id,
                "[asset-mgr] keyframe paid generation budget project_id=%d "
                "max_attempts=%d configured_retries=%d",
                project_id,
                attempt_count,
                configured_retries,
            )
            for attempt in range(attempt_count):
                # 确定性 retry seed：每次 attempt 加偏移，保证 retry 真的换噪声
                retry_seed = (base_seed + attempt * 7919) % (2**31)
                paid_generation_attempts += 1
                try:
                    ref_result = await image_generation_service.generate_keyframe_with_references(
                        event_name=prompt_data.get("event_name", ""),
                        scene_description=prompt_data.get("scene_description", ""),
                        character_bindings=bindings_with_ref,
                        action=prompt_data.get("action", ""),
                        emotion=prompt_data.get("emotion", ""),
                        final_prompt=self._augment_retry_prompt(
                            augment_prompt_with_scene_contract(
                                prompt_data.get("final_prompt") or prompt_data.get("scene_description", ""),
                                required_scene_elements,
                                missing_elements=(
                                    last_scene_validation.missing_elements
                                    if last_scene_validation is not None
                                    else ()
                                ),
                            ),
                            last_validation,
                            attempt,
                        ),
                        style_fingerprint=style_fingerprint or prompt_data.get("style_fingerprint", ""),
                        seed=retry_seed,
                    )
                except Exception as e:
                    xlog.warn(project_id, "[asset-mgr] keyframe-with-refs exception project_id=%d attempt=%d err=%s", project_id, attempt, e)
                    ref_result = {"success": False, "error": f"refs_exception:{type(e).__name__}"}

                if not ref_result.get("success"):
                    err = ref_result.get("error") or ""
                    # multi_image_unsupported / no_resolvable_reference_images → 直接降级
                    if err in ("multi_image_unsupported", "no_resolvable_reference_images"):
                        break
                    # api 失败也直接降级（重试已在外层做了）
                    break

                # 验收
                try:
                    validation = await validator.validate(
                        keyframe_image_path=ref_result.get("image_path") or ref_result.get("image_url") or "",
                        expected_bindings=bindings_with_ref,
                    )
                except Exception as e:
                    xlog.warn(project_id, "[asset-mgr] keyframe validator raised project_id=%d err=%s", project_id, e)
                    # 验收器自己挂了 → 接受这一版（避免 validator 故障阻断业务）
                    gp = dict(ref_result.get("generation_params") or {})
                    gp["identity_validation"] = {"error": f"validator_exception:{type(e).__name__}"}
                    gp["identity_retry_count"] = attempt
                    gp["paid_generation_attempts"] = paid_generation_attempts
                    gp["identity_fallback_used"] = False
                    ref_result["generation_params"] = gp
                    return ref_result

                gp = dict(ref_result.get("generation_params") or {})
                gp["identity_validation"] = validation.to_dict()
                gp["identity_retry_count"] = attempt
                gp["paid_generation_attempts"] = paid_generation_attempts
                gp["identity_fallback_used"] = False
                ref_result["generation_params"] = gp

                if validation.passed:
                    scene_validation = await scene_validator.validate(
                        keyframe_image_path=ref_result.get("image_path") or ref_result.get("image_url") or "",
                        required_elements=required_scene_elements,
                    )
                    gp["scene_validation"] = scene_validation.to_dict()
                    gp["scene_anchor_contract_version"] = SCENE_ANCHOR_CONTRACT_VERSION
                    ref_result["generation_params"] = gp
                    if scene_validation.passed:
                        return ref_result
                    last_scene_validation = scene_validation
                    xlog.warn(
                        project_id,
                        "[asset-mgr] keyframe scene validation failed project_id=%d attempt=%d/%d missing=%s",
                        project_id, attempt + 1, attempt_count, scene_validation.missing_elements,
                    )
                    continue

                last_validation = validation

                xlog.warn(
                    project_id,
                    "[asset-mgr] keyframe identity validation failed project_id=%d attempt=%d/%d reasons=%s",
                    project_id, attempt + 1, attempt_count, validation.reasons,
                )

            # 全部重试失败 → 下一阶段 sprite_composite

            # --- Stage 2: sprite composite fallback ---
            if background_image_url:
                from pathlib import Path as _SpritePath
                placements = [
                    SpritePlacement(
                        character_name=b.character_name,
                        portrait_path=image_generation_service.output_dir / "portraits" / _SpritePath(b.reference_image_url).name,
                        expected_position=b.expected_position or "center",
                    )
                    for b in bindings_with_ref
                    if b.reference_image_url
                ]
                from datetime import datetime
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_event = "".join(c if c.isalnum() else "_" for c in (prompt_data.get("event_name") or "keyframe"))[:40]
                out_path = image_generation_service.output_dir / "keyframes" / f"kf_sprite_{ts}_{base_seed}_{safe_event}.png"

                from app.services.image_generation_service import IMAGE_BASE_URL
                composite_result = composite_keyframe(
                    background_image_url=background_image_url,
                    placements=placements,
                    output_path=out_path,
                    image_base_url=IMAGE_BASE_URL,
                )
                if composite_result.success:
                    return {
                        "success": True,
                        "image_url": composite_result.image_url,
                        "image_path": str(composite_result.image_path or ""),
                        "render_mode": "sprite_composite",
                        "seed": base_seed,
                        "generation_params": {
                            "schema_version": "keyframe-identity-v2",
                            "render_mode": "sprite_composite",
                            "model": "sprite_composite_local",
                            "seed": base_seed,
                            "style_fingerprint": style_fingerprint or prompt_data.get("style_fingerprint", ""),
                            "identity_contract_version": settings.character_identity_contract_version,
                            "character_bindings": [
                                {
                                    "character_id": b.character_id,
                                    "character_name": b.character_name,
                                    "visual_fingerprint": b.visual_fingerprint,
                                    "reference_asset_id": b.reference_asset_id,
                                    "reference_image_sha256": b.reference_image_sha256,
                                    "expected_position": b.expected_position,
                                }
                                for b in bindings_with_ref
                            ],
                            "reference_image_count": len(bindings_with_ref),
                            "identity_validation": (
                                last_validation.to_dict() if last_validation else {}
                            ),
                            "identity_retry_count": max(0, paid_generation_attempts - 1),
                            "paid_generation_attempts": paid_generation_attempts,
                            "identity_fallback_used": True,
                            "final_prompt": prompt_data.get("final_prompt") or prompt_data.get("scene_description", ""),
                        },
                    }
                xlog.warn(project_id, "[asset-mgr] sprite composite failed project_id=%d err=%s", project_id, composite_result.error)

            # --- Stage 3: 显式纯文字降级 ---
            if settings.keyframe_allow_text_only_fallback:
                legacy = await image_generation_service.generate_keyframe(
                    event_name=prompt_data.get("event_name", ""),
                    scene_description=prompt_data.get("scene_description", ""),
                    characters=prompt_data.get("characters", []),
                    action=prompt_data.get("action", ""),
                    emotion=prompt_data.get("emotion", ""),
                    genre=prompt_data.get("genre"),
                    final_prompt=prompt_data.get("final_prompt"),
                    visual_style_prompt=prompt_data.get("visual_style_prompt"),
                )
                if legacy.get("success"):
                    gp = dict(legacy.get("generation_params") or {})
                    gp["schema_version"] = "keyframe-identity-v2"
                    gp["render_mode"] = "text_only_degraded"
                    gp["identity_fallback_used"] = True
                    gp["identity_retry_count"] = max(0, paid_generation_attempts - 1)
                    gp["paid_generation_attempts"] = paid_generation_attempts
                    gp["seed"] = base_seed
                    gp["style_fingerprint"] = style_fingerprint or prompt_data.get("style_fingerprint", "")
                    gp["identity_contract_version"] = settings.character_identity_contract_version
                    legacy["generation_params"] = gp
                    return legacy

            # D07 — 所有兜底失败 → 返回失败，禁止把错脸图保存为 completed
            return {
                "success": False,
                "error": "identity_validation_failed_all_stages",
                "render_mode": "failed",
                "last_validation": last_validation.to_dict() if last_validation else {},
                "identity_retry_count": max(0, paid_generation_attempts - 1),
                "paid_generation_attempts": paid_generation_attempts,
            }

        # --- 没有任何 reference binding → 走旧 text 路径 ---
        legacy = await image_generation_service.generate_keyframe(
            event_name=prompt_data.get("event_name", ""),
            scene_description=prompt_data.get("scene_description", ""),
            characters=prompt_data.get("characters", []),
            action=prompt_data.get("action", ""),
            emotion=prompt_data.get("emotion", ""),
            genre=prompt_data.get("genre"),
            final_prompt=prompt_data.get("final_prompt"),
            visual_style_prompt=prompt_data.get("visual_style_prompt"),
        )
        if not legacy.get("success"):
            return legacy

        legacy_params = dict(legacy.get("generation_params") or {})
        legacy_params["schema_version"] = "keyframe-identity-v2"
        legacy_params["render_mode"] = "text_only_no_refs"
        legacy_params["identity_fallback_used"] = True
        legacy_params["seed"] = base_seed
        legacy_params["style_fingerprint"] = style_fingerprint or prompt_data.get("style_fingerprint", "")
        legacy_params["identity_contract_version"] = settings.character_identity_contract_version
        legacy_params["character_bindings"] = [
            {
                "character_id": b.character_id,
                "character_name": b.character_name,
                "visual_fingerprint": b.visual_fingerprint,
                "reference_asset_id": b.reference_asset_id,
                "reference_image_sha256": b.reference_image_sha256,
                "requested_emotion": b.requested_emotion,
                "requested_outfit": b.requested_outfit,
                "requested_pose": b.requested_pose,
                "expected_position": b.expected_position,
            }
            for b in bindings_typed
        ]
        legacy["generation_params"] = legacy_params
        return legacy

    def _augment_retry_prompt(
        self,
        base_prompt: str,
        last_validation: Optional[Any],
        attempt: int,
    ) -> str:
        """D05 — 根据上一次验收失败原因构造针对性 retry prompt。"""
        if attempt <= 0 or last_validation is None:
            return base_prompt
        reasons = getattr(last_validation, "reasons", []) or []
        prefix_lines = [
            f"IDENTITY RETRY (attempt {attempt + 1}). Previous attempt failed identity validation:",
            ", ".join(reasons) if reasons else "(unknown)",
            "Regenerate using the supplied reference images as mandatory identity anchors.",
            "Keep exactly the same face shape, hairstyle, hair color, apparent age, "
            "gender presentation, body build, signature accessories and canonical outfit.",
            "Change only pose, expression, viewing angle and minor cloth movement.",
            "Do NOT swap identities between characters. Do NOT blend faces.",
        ]
        return "\n".join(prefix_lines) + "\n\n" + (base_prompt or "")

    async def generate_chapter_keyframes(
        self,
        project_id: int,
        chapter_index: int
    ) -> Dict[str, Any]:
        """
        生成章节关键帧

        Args:
            project_id: 项目 ID
            chapter_index: 章节索引

        Returns:
            {
                "total": int,
                "generated": int,
                "failed": int,
                "results": [...]
            }
        """
        xlog.info(project_id, "[asset-mgr] keyframes start project_id=%d chapter_index=%d", project_id, chapter_index)
        outline = self._get_chapter_outline(project_id, chapter_index)
        content = self._get_chapter_content(project_id, chapter_index)
        story_bible = self._get_story_bible(project_id)

        if not outline:
            xlog.warn(project_id, "[asset-mgr] keyframes missing outline project_id=%d chapter_index=%d", project_id, chapter_index)
            return {"total": 0, "generated": 0, "failed": 0, "results": [], "error": "章节大纲不存在"}
        if not story_bible:
            xlog.warn(project_id, "[asset-mgr] keyframes missing story bible project_id=%d chapter_index=%d", project_id, chapter_index)
            return {"total": 0, "generated": 0, "failed": 0, "results": [], "error": "StoryBible 不存在"}

        # 构建 prompts
        outline_json = {
            "scene": outline.scene,
            "conflict": outline.conflict,
            "emotion": outline.emotion,
            "characters": outline.characters,
            "title": outline.title,
            "summary": outline.summary,
            "visual_keywords": outline.visual_keywords
        }
        content_text = content.content if content else ""
        # 浅拷贝避免污染 ORM 实例的 raw_json JSON 列；注入真实 project_id，让下游
        # md5(name|pid)[:12] 能与 identity_master portrait 算出的 cid 匹配。
        sb_raw = dict(story_bible.raw_json) if story_bible.raw_json else {}
        sb_raw["project_id"] = project_id
        # project-visual-bible-v2 §B6 — lock the project bible at generate entry
        bible = self._ensure_visual_bible_locked(project_id, story_bible)
        visual_style_profile = self._build_visual_style_profile(project_id, story_bible)
        sb_raw = source_visual_profile_service.apply_character_overrides(
            sb_raw,
            visual_style_profile.source_profile_id,
        )

        # P1.3 — 先调 KeyframeMomentSelector 从正文挑最有画面感的 2-3 个时刻；
        # 若拿到则用 moments 构造 prompt，否则 fallback 走旧 prompt builder（单关键帧 = 章节标题/场景）。
        # ``KEYFRAME_AUTO_SELECT=0`` 会在 selector 内部禁用 LLM，selector 返回空 → 自动 fallback。
        kf_prompts: List[Dict[str, Any]] = []
        moment_source = "legacy"
        moments: List[Any] = []

        # C06 — Stage_Keyframe_Identity_AR_Blueprint §8：portraits before keyframes。
        # 解析（或补生）每个 outline 出场角色的身份母版，得到 contracts 映射，
        # 之后 _build_keyframe_prompts_from_moments 会把完整 binding 注入 prompt_data。
        # 失败时退化为无 contracts，wrapper 会走 text_only 路径。
        identity_contracts: Dict[str, Any] = {}
        normalized_sb: Dict[str, Any] = {}
        try:
            from app.services.character_visual_profile_service import normalize_story_bible
            from app.services.identity_master_resolver import (
                generate_or_resolve_identity_master_portraits,
            )
            normalized_sb = normalize_story_bible(dict(sb_raw), context_text=content_text)
            normalized_sb = source_visual_profile_service.apply_character_overrides(
                normalized_sb,
                visual_style_profile.source_profile_id,
            )
            normalized_sb["project_id"] = project_id
            outline_chars = list(outline_json.get("characters") or [])[:3]
            relevant_chars = [
                c for c in normalized_sb.get("characters", [])
                if (c.get("name") or "").strip() in [str(n).strip() for n in outline_chars]
            ][:3]
            if relevant_chars:
                resolutions = await generate_or_resolve_identity_master_portraits(
                    self.db,
                    project_id=project_id,
                    characters=relevant_chars,
                    style_fingerprint=visual_style_profile.fingerprint,
                    visual_style_prompt=visual_style_profile.keyframe_prompt_en,
                )
                for char_id, res in resolutions.items():
                    identity_contracts[char_id] = res.contract
                missing_refs = [cid for cid, res in resolutions.items() if res.asset is None]
                if missing_refs:
                    xlog.warn(
                        project_id,
                        "[asset-mgr] keyframe identity master missing project_id=%d chapter_index=%d missing=%d/%d",
                        project_id, chapter_index, len(missing_refs), len(resolutions),
                    )
        except Exception as e:
            import traceback as _tb
            xlog.warn(project_id, "[asset-mgr] identity master resolve failed project_id=%d err=%s\n%s", project_id, e, _tb.format_exc())

        if content_text:
            try:
                from app.services.keyframe_moment_selector import KeyframeMomentSelector
                selector = KeyframeMomentSelector()
                moments = await selector.select(
                    content_text,
                    outline_json,
                    max_keyframes=KEYFRAME_AUTO_SELECT_MAX,
                )
            except Exception as e:
                xlog.warn(project_id, "[asset-mgr] keyframe_moment selector failed project_id=%d chapter_index=%d err=%s", project_id, chapter_index, e)
                moments = []

        # A single canonical character can appear in multiple time periods.
        # Resolve an additional master for every explicit moment-level age
        # stage, while keeping the canonical character id unchanged.
        if moments and normalized_sb:
            try:
                from app.services.identity_master_resolver import (
                    generate_or_resolve_identity_master_portraits,
                )

                normalized_by_name = {
                    str(c.get("name") or "").strip(): c
                    for c in normalized_sb.get("characters", [])
                    if isinstance(c, dict) and str(c.get("name") or "").strip()
                }
                resolved_variant_ids: set[str] = set()
                for moment in moments:
                    get_age = getattr(moment, "get_age_group", None)
                    for name in list(getattr(moment, "target_characters", []) or [])[:3]:
                        requested_age = get_age(name) if callable(get_age) else ""
                        if not requested_age:
                            continue
                        base_char = normalized_by_name.get(str(name).strip())
                        if not base_char:
                            continue
                        canonical_id = prompt_builder_service.generate_character_id(name, project_id)
                        age_contract = age_contract_from_profile(
                            base_char.get("visual_profile"),
                            override=requested_age,
                            age_source="keyframe_moment",
                            source_excerpt=str(getattr(moment, "source_excerpt", "") or ""),
                        )
                        variant_id = identity_variant_id(canonical_id, age_contract.age_group)
                        if variant_id in resolved_variant_ids:
                            continue
                        variant_char = dict(base_char)
                        variant_char["character_id"] = canonical_id
                        variant_char = apply_age_contract_to_character(variant_char, age_contract)
                        variant_resolutions = await generate_or_resolve_identity_master_portraits(
                            self.db,
                            project_id=project_id,
                            characters=[variant_char],
                            style_fingerprint=visual_style_profile.fingerprint,
                            visual_style_prompt=visual_style_profile.keyframe_prompt_en,
                        )
                        resolution = variant_resolutions.get(canonical_id)
                        if resolution is not None:
                            identity_contracts[variant_id] = resolution.contract
                        resolved_variant_ids.add(variant_id)
            except Exception as e:
                xlog.warn(
                    project_id,
                    "[asset-mgr] age-variant identity resolve failed project_id=%d chapter_index=%d err=%s",
                    project_id,
                    chapter_index,
                    e,
                )

        if moments:
            try:
                kf_prompts = await self._build_keyframe_prompts_from_moments(
                    moments, outline_json, sb_raw,
                    visual_style_profile=visual_style_profile,
                    contracts=identity_contracts,
                )
                moment_source = "auto_select"
            except Exception as e:
                xlog.warn(project_id, "[asset-mgr] keyframe_moment prompt build failed project_id=%d chapter_index=%d err=%s", project_id, chapter_index, e)
                kf_prompts = []

        if not kf_prompts:
            kf_prompts = await prompt_builder_service.build_keyframe_prompts_async(
                content_text,
                outline_json,
                sb_raw,
                visual_style_profile=visual_style_profile,
            )
            if moments:
                moment_source = "fallback_build"
            else:
                moment_source = "legacy"

        xlog.info(
            project_id,
            "[asset-mgr] keyframes prompt built project_id=%d chapter_index=%d count=%d source=%s",
            project_id, chapter_index, len(kf_prompts), moment_source,
        )

        results = []
        failed = 0
        generated = 0

        from app.core.config import get_settings
        _settings = get_settings()
        _parallel_kf = _settings.parallel_keyframe_generation

        # E03 + E04 — compute v2 cache key per prompt + look up existing Asset
        # for this event_name. If found and not stale, skip regeneration.
        _kf_model = _settings.keyframe_image_model or ""
        _kf_contract_v = _settings.character_identity_contract_version
        _kf_validator_v = _settings.keyframe_identity_validator_version

        def _check_existing_keyframe(prompt_data):
            """Return (existing_asset_or_None, stale_reason_or_None, stale_asset_or_None)."""
            event_name = prompt_data.get("event_name")
            if not event_name:
                return None, None, None
            candidates = self.db.query(Asset).filter(
                Asset.project_id == project_id,
                Asset.asset_type == "keyframe",
                Asset.chapter_index == chapter_index,
                Asset.event_name == event_name,
                Asset.status == "completed",
            ).all()
            if not candidates:
                return None, None, None
            expected_key = self._compute_keyframe_cache_key_v2(
                prompt_data,
                identity_contract_version=_kf_contract_v,
                keyframe_image_model=_kf_model,
                validator_version=_kf_validator_v,
            )
            for cand in candidates:
                params = cand.generation_params or {}
                if not isinstance(params, dict):
                    continue
                # strict match — same cache_key_v2 ⇒ reuse
                if params.get("cache_key_v2") == expected_key:
                    return cand, None, None
            # otherwise pick first candidate and report staleness
            first = candidates[0]
            stale_reason = self._is_keyframe_stale(
                first,
                prompt_data,
                expected_cache_key=expected_key,
                expected_style_fingerprint=(prompt_data.get("style_fingerprint") or "").strip(),
                expected_contract_version=_kf_contract_v,
                expected_model=_kf_model,
                expected_validator_version=_kf_validator_v,
            )
            return None, stale_reason, first

        if _parallel_kf:
            # === 两段式：阶段 A 并发调供应商（无 DB 写）===
            async def _gen_one_kf(prompt_data):
                start_time = time.time()
                result = await self._generate_keyframe_with_identity_pipeline(
                    project_id=project_id,
                    chapter_index=chapter_index,
                    prompt_data=prompt_data,
                    bible=bible,
                    style_fingerprint=visual_style_profile.fingerprint,
                )
                generation_time = time.time() - start_time
                return result, generation_time

            kf_outcomes = await asyncio.gather(
                *[_gen_one_kf(p) for p in kf_prompts],
                return_exceptions=True,
            )

            # === 阶段 B：串行持久化（主 session 单协程）===
            for prompt_data, outcome in zip(kf_prompts, kf_outcomes):
                stat = None
                try:
                    # E03/E04 — cache check before persisting
                    existing_kf, stale_reason, stale_asset = _check_existing_keyframe(prompt_data)
                    if existing_kf is not None:
                        generated += 1
                        xlog.info(project_id, "[asset-mgr] keyframe cache hit project_id=%d chapter_index=%d event=%s asset_id=%d", project_id, chapter_index, prompt_data.get("event_name"), existing_kf.id)
                        results.append({
                            "success": True,
                            "cached": True,
                            "asset_id": existing_kf.id,
                            "event_name": prompt_data.get("event_name"),
                            "image_url": existing_kf.image_url,
                        })
                        continue
                    if stale_asset is not None and stale_reason:
                        xlog.info(project_id, "[asset-mgr] keyframe cache stale project_id=%d asset_id=%d event=%s reason=%s", project_id, stale_asset.id, prompt_data.get("event_name"), stale_reason)

                    if isinstance(outcome, Exception):
                        stat = self.stat_service.start_generation(
                            project_id=project_id,
                            generation_type="keyframe",
                            chapter_index=chapter_index,
                            target_name=prompt_data["event_name"],
                            variation_info={"emotion": prompt_data.get("emotion")},
                        )
                        self.stat_service.end_generation(stat.id, success=False, error_message=str(outcome))
                        failed += 1
                        xlog.error(project_id, outcome, "[asset-mgr] keyframe exception project_id=%d chapter_index=%d event=%s", project_id, chapter_index, prompt_data.get("event_name", "unknown"))
                        results.append({
                            "success": False,
                            "event_name": prompt_data.get("event_name", "unknown"),
                            "error": str(outcome),
                        })
                        continue

                    result, generation_time = outcome
                    stat = self.stat_service.start_generation(
                        project_id=project_id,
                        generation_type="keyframe",
                        chapter_index=chapter_index,
                        target_name=prompt_data["event_name"],
                        variation_info={"emotion": prompt_data.get("emotion")},
                    )

                    if result["success"]:
                        self.stat_service.end_generation(stat.id, success=True)
                        _kf_cache_key = self._compute_keyframe_cache_key_v2(
                            prompt_data,
                            identity_contract_version=_kf_contract_v,
                            keyframe_image_model=_kf_model,
                            validator_version=_kf_validator_v,
                        )
                        # 若存在 stale 候选，覆盖它而不是新建一行
                        asset = stale_asset or Asset(
                            project_id=project_id,
                            chapter_index=chapter_index,
                            asset_type="keyframe",
                        )
                        asset.project_id = project_id
                        asset.chapter_index = chapter_index
                        asset.asset_type = "keyframe"
                        asset.target_name = prompt_data["event_name"]
                        # 保留 source_excerpt 原文锚点，避免 visual_focus 改写文
                        # 导致后续资源匹配漂移；旧数据回退到 scene_description。
                        asset.prompt = (
                            prompt_data.get("_keyframe_moment_excerpt")
                            or prompt_data["scene_description"]
                        )
                        asset.image_url = result["image_url"]
                        asset.status = "completed"
                        asset.event_name = prompt_data["event_name"]
                        asset.genre = prompt_data.get("genre")
                        gen_params = dict(result.get("generation_params") or {
                            "schema_version": "keyframe-identity-v2",
                            "project_genre": prompt_data.get("project_genre"),
                            "style_fingerprint": prompt_data.get("style_fingerprint"),
                            "visual_style_profile": prompt_data.get("visual_style_profile"),
                            "visual_style_prompt": prompt_data.get("visual_style_prompt"),
                            "final_prompt": result.get("prompt") or prompt_data.get("final_prompt"),
                        })
                        gen_params["cache_key_v2"] = _kf_cache_key
                        gen_params["identity_validator_version"] = _kf_validator_v
                        # Stage_VNGraph_Assembly_AR — paragraph 锚点字段
                        gen_params["source_excerpt"] = (
                            prompt_data.get("_keyframe_moment_excerpt") or ""
                        )
                        gen_params["moment_summary"] = (
                            prompt_data.get("_keyframe_moment_summary")
                            or prompt_data.get("event_name") or ""
                        )
                        gen_params["visual_focus"] = prompt_data["scene_description"]
                        _moment_fp_seed = (
                            str(gen_params["moment_summary"])
                            + "|" + str(gen_params["visual_focus"])
                        )
                        gen_params["moment_fingerprint"] = hashlib.md5(
                            _moment_fp_seed.encode("utf-8")
                        ).hexdigest()[:16]
                        gen_params["target_character_ids"] = (
                            prompt_data.get("_keyframe_target_character_ids") or []
                        )
                        gen_params["suggested_paragraph_index"] = None
                        asset.generation_params = self._stamp_visual_bible_params(
                            gen_params,
                            bible,
                            scene_selector=prompt_data.get("event_name"),
                            project_id=project_id,
                        )
                        asset.generation_time = generation_time
                        asset.processed = 0
                        self.db.add(asset)
                        self.db.commit()
                        generated += 1
                        xlog.info(project_id, "[asset-mgr] keyframe generated project_id=%d chapter_index=%d asset_id=%d event=%s elapsed_ms=%d", project_id, chapter_index, asset.id, prompt_data["event_name"], int(generation_time * 1000))
                        results.append({
                            "success": True,
                            "asset_id": asset.id,
                            "event_name": prompt_data["event_name"],
                            "image_url": result["image_url"],
                            "generation_time": generation_time,
                        })
                    else:
                        self.stat_service.end_generation(stat.id, success=False, error_message=result.get("error"))
                        failed += 1
                        xlog.warn(project_id, "[asset-mgr] keyframe failed project_id=%d chapter_index=%d event=%s error=%s", project_id, chapter_index, prompt_data["event_name"], result.get("error"))
                        results.append({
                            "success": False,
                            "event_name": prompt_data["event_name"],
                            "error": result.get("error", "生成失败"),
                            "generation_time": generation_time,
                        })
                except Exception as e:
                    if stat:
                        self.stat_service.end_generation(stat.id, success=False, error_message=str(e))
                    failed += 1
                    xlog.error(project_id, e, "[asset-mgr] keyframe exception project_id=%d chapter_index=%d event=%s", project_id, chapter_index, prompt_data.get("event_name", "unknown"))
                    results.append({
                        "success": False,
                        "event_name": prompt_data.get("event_name", "unknown"),
                        "error": str(e),
                    })
        else:
            # === 串行路径（原行为，默认）===
            for prompt_data in kf_prompts:
                stat = None
                try:
                    # E03/E04 — cache check before paying for regeneration
                    existing_kf, stale_reason, stale_asset = _check_existing_keyframe(prompt_data)
                    if existing_kf is not None:
                        generated += 1
                        xlog.info(project_id, "[asset-mgr] keyframe cache hit project_id=%d chapter_index=%d event=%s asset_id=%d", project_id, chapter_index, prompt_data.get("event_name"), existing_kf.id)
                        results.append({
                            "success": True,
                            "cached": True,
                            "asset_id": existing_kf.id,
                            "event_name": prompt_data.get("event_name"),
                            "image_url": existing_kf.image_url,
                        })
                        continue
                    if stale_asset is not None and stale_reason:
                        xlog.info(project_id, "[asset-mgr] keyframe cache stale project_id=%d asset_id=%d event=%s reason=%s", project_id, stale_asset.id, prompt_data.get("event_name"), stale_reason)

                    # 开始记录生成时间
                    stat = self.stat_service.start_generation(
                        project_id=project_id,
                        generation_type="keyframe",
                        chapter_index=chapter_index,
                        target_name=prompt_data["event_name"],
                        variation_info={"emotion": prompt_data.get("emotion")}
                    )

                    start_time = time.time()

                    result = await self._generate_keyframe_with_identity_pipeline(
                        project_id=project_id,
                        chapter_index=chapter_index,
                        prompt_data=prompt_data,
                        bible=bible,
                        style_fingerprint=visual_style_profile.fingerprint,
                    )

                    generation_time = time.time() - start_time

                    if result["success"]:
                        self.stat_service.end_generation(stat.id, success=True)
                        _kf_cache_key = self._compute_keyframe_cache_key_v2(
                            prompt_data,
                            identity_contract_version=_kf_contract_v,
                            keyframe_image_model=_kf_model,
                            validator_version=_kf_validator_v,
                        )
                        asset = stale_asset or Asset(
                            project_id=project_id,
                            chapter_index=chapter_index,
                            asset_type="keyframe",
                        )
                        asset.project_id = project_id
                        asset.chapter_index = chapter_index
                        asset.asset_type = "keyframe"
                        asset.target_name = prompt_data["event_name"]
                        # 保留 source_excerpt 原文锚点，避免 visual_focus 改写文
                        # 导致后续资源匹配漂移；旧数据回退到 scene_description。
                        asset.prompt = (
                            prompt_data.get("_keyframe_moment_excerpt")
                            or prompt_data["scene_description"]
                        )
                        asset.image_url = result["image_url"]
                        asset.status = "completed"
                        asset.event_name = prompt_data["event_name"]
                        asset.genre = prompt_data.get("genre")
                        gen_params = dict(result.get("generation_params") or {
                            "schema_version": "keyframe-identity-v2",
                            "project_genre": prompt_data.get("project_genre"),
                            "style_fingerprint": prompt_data.get("style_fingerprint"),
                            "visual_style_profile": prompt_data.get("visual_style_profile"),
                            "visual_style_prompt": prompt_data.get("visual_style_prompt"),
                            "final_prompt": result.get("prompt") or prompt_data.get("final_prompt"),
                        })
                        gen_params["cache_key_v2"] = _kf_cache_key
                        gen_params["identity_validator_version"] = _kf_validator_v
                        # Stage_VNGraph_Assembly_AR — paragraph 锚点字段
                        gen_params["source_excerpt"] = (
                            prompt_data.get("_keyframe_moment_excerpt") or ""
                        )
                        gen_params["moment_summary"] = (
                            prompt_data.get("_keyframe_moment_summary")
                            or prompt_data.get("event_name") or ""
                        )
                        gen_params["visual_focus"] = prompt_data["scene_description"]
                        _moment_fp_seed = (
                            str(gen_params["moment_summary"])
                            + "|" + str(gen_params["visual_focus"])
                        )
                        gen_params["moment_fingerprint"] = hashlib.md5(
                            _moment_fp_seed.encode("utf-8")
                        ).hexdigest()[:16]
                        gen_params["target_character_ids"] = (
                            prompt_data.get("_keyframe_target_character_ids") or []
                        )
                        gen_params["suggested_paragraph_index"] = None
                        asset.generation_params = self._stamp_visual_bible_params(
                            gen_params,
                            bible,
                            scene_selector=prompt_data.get("event_name"),
                            project_id=project_id,
                        )
                        asset.generation_time = generation_time
                        asset.processed = 0
                        self.db.add(asset)
                        self.db.commit()
                        generated += 1
                        xlog.info(project_id, "[asset-mgr] keyframe generated project_id=%d chapter_index=%d asset_id=%d event=%s elapsed_ms=%d", project_id, chapter_index, asset.id, prompt_data["event_name"], int(generation_time * 1000))
                        results.append({
                            "success": True,
                            "asset_id": asset.id,
                            "event_name": prompt_data["event_name"],
                            "image_url": result["image_url"],
                            "generation_time": generation_time
                        })
                    else:
                        self.stat_service.end_generation(stat.id, success=False, error_message=result.get("error"))
                        failed += 1
                        xlog.warn(project_id, "[asset-mgr] keyframe failed project_id=%d chapter_index=%d event=%s error=%s", project_id, chapter_index, prompt_data["event_name"], result.get("error"))
                        results.append({
                            "success": False,
                            "event_name": prompt_data["event_name"],
                            "error": result.get("error", "生成失败"),
                            "generation_time": generation_time
                        })

                except Exception as e:
                    if stat:
                        self.stat_service.end_generation(stat.id, success=False, error_message=str(e))
                    failed += 1
                    xlog.error(project_id, e, "[asset-mgr] keyframe exception project_id=%d chapter_index=%d event=%s", project_id, chapter_index, prompt_data.get("event_name", "unknown"))
                    results.append({
                        "success": False,
                        "event_name": prompt_data.get("event_name", "unknown"),
                        "error": str(e)
                    })

        xlog.info(project_id, "[asset-mgr] keyframes done project_id=%d chapter_index=%d total=%d generated=%d failed=%d", project_id, chapter_index, len(kf_prompts), generated, failed)
        return {
            "total": len(kf_prompts),
            "generated": generated,
            "failed": failed,
            "moment_source": moment_source,
            "total_time": sum(r.get("generation_time", 0) for r in results),
            "avg_time": sum(r.get("generation_time", 0) for r in results if r.get("generation_time")) / generated if generated > 0 else 0,
            "results": results
        }

    async def generate_all_assets(
        self,
        project_id: int,
        portrait_variations: Optional[List[Dict[str, str]]] = None,
        background_moods: Optional[List[str]] = None,
        auto_demand: bool = True,
    ) -> Dict[str, Any]:
        """
        一键生成所有素材

        Args:
            project_id: 项目 ID
            portrait_variations: 立绘变体配置（auto_demand=True 时忽略）
            background_moods: 背景氛围配置
            auto_demand: 内容驱动模式（默认 True，复用 ``generate_all_portraits`` 的语义）

        Returns:
            {
                "portraits": {...},
                "backgrounds": {...},
                "keyframes": {...},
                "total_generated": int
            }
        """
        xlog.info(project_id, "[asset-mgr] generate all start project_id=%d auto_demand=%s", project_id, auto_demand)
        # 生成所有立绘
        portraits_result = await self.generate_all_portraits(
            project_id=project_id,
            variations=portrait_variations,
            auto_demand=auto_demand,
        )

        # 获取所有章节
        outlines = self.db.query(ChapterOutline).filter(
            ChapterOutline.project_id == project_id
        ).all()

        backgrounds_results = []
        keyframes_results = []

        for outline in outlines:
            # 生成背景
            bg_result = await self.generate_chapter_backgrounds_v2(
                project_id=project_id,
                chapter_index=outline.chapter_index,
                moods=background_moods
            )
            backgrounds_results.extend(bg_result.get("results", []))

            # 生成关键帧
            kf_result = await self.generate_chapter_keyframes(
                project_id=project_id,
                chapter_index=outline.chapter_index
            )
            keyframes_results.extend(kf_result.get("results", []))

        total_generated = (
            portraits_result.get("generated", 0) +
            len([r for r in backgrounds_results if r.get("success")]) +
            len([r for r in keyframes_results if r.get("success")])
        )

        total_time = (
            portraits_result.get("total_time", 0) +
            sum(r.get("generation_time", 0) for r in backgrounds_results) +
            sum(r.get("generation_time", 0) for r in keyframes_results)
        )
        xlog.info(project_id, "[asset-mgr] generate all done project_id=%d total_generated=%d total_time_ms=%d", project_id, total_generated, int(total_time * 1000))

        return {
            "portraits": portraits_result,
            "backgrounds": {
                "total": len(backgrounds_results),
                "generated": len([r for r in backgrounds_results if r.get("success")]),
                "failed": len([r for r in backgrounds_results if not r.get("success")]),
                "total_time": sum(r.get("generation_time", 0) for r in backgrounds_results),
                "avg_time": sum(r.get("generation_time", 0) for r in backgrounds_results if r.get("generation_time")) / len([r for r in backgrounds_results if r.get("generation_time")]) if any(r.get("generation_time") for r in backgrounds_results) else 0,
                "results": backgrounds_results
            },
            "keyframes": {
                "total": len(keyframes_results),
                "generated": len([r for r in keyframes_results if r.get("success")]),
                "failed": len([r for r in keyframes_results if not r.get("success")]),
                "total_time": sum(r.get("generation_time", 0) for r in keyframes_results),
                "avg_time": sum(r.get("generation_time", 0) for r in keyframes_results if r.get("generation_time")) / len([r for r in keyframes_results if r.get("generation_time")]) if any(r.get("generation_time") for r in keyframes_results) else 0,
                "results": keyframes_results
            },
            "total_generated": total_generated,
            "total_time": total_time
        }

    def _select_portrait_cache_candidate(
        self,
        candidates: List[Asset],
        prompt_data: Dict[str, Any],
    ) -> tuple[Optional[Asset], Optional[Asset]]:
        """Pick a cache hit without overwriting a different age variant.

        The relational columns intentionally keep the canonical character id.
        Age-stage identity lives in ``generation_params`` so multiple stages
        can coexist for the same emotion/outfit/pose combination.
        """
        for candidate in candidates:
            if self._portrait_asset_matches_prompt_contract(candidate, prompt_data):
                return candidate, None

        expected_variant = str(prompt_data.get("identity_variant_id") or "").strip()
        expected_age = str(prompt_data.get("age_group") or "").strip()
        expected_visual = str(prompt_data.get("visual_fingerprint") or "").strip()
        for candidate in candidates:
            params = candidate.generation_params if isinstance(candidate.generation_params, dict) else {}
            same_variant = expected_variant and params.get("identity_variant_id") == expected_variant
            legacy_same_visual = (
                expected_visual
                and params.get("visual_fingerprint") == expected_visual
                and (not expected_age or params.get("age_group") in {None, "", expected_age})
            )
            if same_variant or legacy_same_visual:
                return None, candidate
        return None, None

    def _portrait_asset_matches_prompt_contract(self, asset: Asset, prompt_data: Dict[str, Any]) -> bool:
        """已有立绘是否满足当前 prompt 契约。

        旧版资产可能只有 character_id/emotion/outfit/pose 相同，但 prompt 里没有性别锚点。
        这种情况下必须重新生成，否则“修了男女不分”后仍会一直复用旧图。
        """
        params = asset.generation_params or {}
        stored_contract = params.get("portrait_generation_contract_version")
        if stored_contract != PORTRAIT_GENERATION_CONTRACT_VERSION:
            return False
        alpha_meta = params.get("portrait_alpha")
        if not isinstance(alpha_meta, dict) or not bool(alpha_meta.get("passed")):
            return False
        if params.get("prompt_source") != "llm_rewriter":
            return False
        if (
            params.get("portrait_final_prompt_contract_version")
            != PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION
        ):
            return False
        if params.get("requested_shot") != "full_body":
            return False
        expected_style = (prompt_data.get("style_fingerprint") or "").strip()
        if expected_style and params.get("style_fingerprint") != expected_style:
            return False

        expected_visual = (prompt_data.get("visual_fingerprint") or "").strip()
        if expected_visual and params.get("visual_fingerprint") != expected_visual:
            return False

        expected_canonical = (
            prompt_data.get("canonical_identity_fingerprint") or ""
        ).strip()
        if (
            expected_canonical
            and params.get("canonical_identity_fingerprint") != expected_canonical
        ):
            return False

        expected_variant = (prompt_data.get("identity_variant_id") or "").strip()
        if expected_variant and params.get("identity_variant_id") != expected_variant:
            return False

        expected_age = (prompt_data.get("age_group") or "").strip()
        if expected_age and params.get("age_group") != expected_age:
            return False
        if prompt_data.get("age_contract"):
            stored_age_contract = params.get("age_contract")
            if not isinstance(stored_age_contract, dict):
                return False
            if stored_age_contract.get("version") != prompt_data["age_contract"].get("version"):
                return False
            age_validation = params.get("age_validation")
            if not isinstance(age_validation, dict) or not bool(age_validation.get("passed")):
                return False

        expected_anchor = (prompt_data.get("character_visual_anchor") or "").strip()
        if expected_anchor and params.get("character_visual_anchor") != expected_anchor:
            return False

        expected_gender = (prompt_data.get("gender") or "").strip()
        if not expected_gender and not (prompt_data.get("gender_prompt") or "").strip():
            return True

        stored_gender = (params.get("gender") or "").strip()
        if expected_gender and stored_gender != expected_gender:
            return False

        # The LLM may express gender naturally ("adult man", "male
        # character", etc.). The authoritative structured gender stamp is the
        # stable cache key; do not require one backend-authored phrase inside
        # the LLM prose.
        return True

    def _asset_matches_style_contract(self, asset: Asset, prompt_data: Dict[str, Any]) -> bool:
        expected_style = (prompt_data.get("style_fingerprint") or "").strip()
        if not expected_style:
            return True
        params = asset.generation_params or {}
        return params.get("style_fingerprint") == expected_style

    def _compute_keyframe_cache_key_v2(
        self,
        prompt_data: Dict[str, Any],
        *,
        identity_contract_version: str,
        keyframe_image_model: str,
        validator_version: str,
    ) -> str:
        """E03 — keyframe v2 cache key.

        Combines the 7 dimensions that affect keyframe identity output:
        - schema/model (keyframe_image_model + contract + validator)
        - prompt (final_prompt + scene_description + action + emotion)
        - seed (base seed for this event)
        - style_fingerprint (project)
        - all character_ids + visual_fingerprints
        - all reference_asset_ids + reference_image_sha256
        - outfit/emotion/pose per binding (E05 will populate these)
        """
        import hashlib as _hashlib
        import json as _json
        from app.services.keyframe_scene_validator_service import (
            VERSION as SCENE_ANCHOR_CONTRACT_VERSION,
            extract_required_scene_elements,
        )

        bindings = prompt_data.get("character_bindings") or []
        binding_tuples = []
        for b in bindings:
            if hasattr(b, "character_id"):
                binding_tuples.append((
                    b.character_id,
                    getattr(b, "visual_fingerprint", "") or "",
                    getattr(b, "reference_asset_id", 0) or 0,
                    getattr(b, "reference_image_sha256", "") or "",
                    getattr(b, "expected_position", "") or "",
                    getattr(b, "requested_outfit", "") or "",
                    getattr(b, "requested_emotion", "") or "",
                    getattr(b, "requested_pose", "") or "",
                    getattr(b, "requested_action", "") or "",
                    getattr(b, "injury_state", "") or "",
                    getattr(b, "held_item", "") or "",
                ))
            elif isinstance(b, dict):
                binding_tuples.append((
                    b.get("character_id", ""),
                    b.get("visual_fingerprint", ""),
                    b.get("reference_asset_id", 0),
                    b.get("reference_image_sha256", ""),
                    b.get("expected_position", ""),
                    b.get("requested_outfit", "") or b.get("outfit", ""),
                    b.get("requested_emotion", "") or b.get("emotion", ""),
                    b.get("requested_pose", "") or b.get("pose", ""),
                    b.get("requested_action", "") or b.get("action", ""),
                    b.get("injury_state", ""),
                    b.get("held_item", ""),
                ))
        binding_tuples.sort()

        key_payload = {
            "schema": "keyframe-cache-v2",
            "model": (keyframe_image_model or "").strip(),
            "contract_version": identity_contract_version,
            "validator_version": validator_version,
            "style_fingerprint": (prompt_data.get("style_fingerprint") or "").strip(),
            "final_prompt": (prompt_data.get("final_prompt") or "").strip(),
            "scene_description": (prompt_data.get("scene_description") or "").strip(),
            "action": (prompt_data.get("action") or "").strip(),
            "emotion": (prompt_data.get("emotion") or "").strip(),
            "event_name": (prompt_data.get("event_name") or "").strip(),
            "seed": prompt_data.get("seed"),
            "bindings": binding_tuples,
            "required_scene_elements": [
                element.to_dict()
                for element in extract_required_scene_elements(prompt_data)
            ],
            "scene_anchor_contract_version": SCENE_ANCHOR_CONTRACT_VERSION,
        }
        blob = _json.dumps(key_payload, sort_keys=True, ensure_ascii=False)
        return _hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _is_keyframe_stale(
        self,
        asset: Asset,
        prompt_data: Dict[str, Any],
        *,
        expected_cache_key: str,
        expected_style_fingerprint: str,
        expected_contract_version: str,
        expected_model: str,
        expected_validator_version: str,
    ) -> Optional[str]:
        """E04 — returns reason string if the existing keyframe Asset is stale.

        Stale reasons (7 dimensions from blueprint §13):
        1. cache_key_v2 mismatch (covers prompt / seed / style / bindings /
           refs / outfit / emotion / pose — all in one hash)
        2. style_fingerprint mismatch
        3. identity_contract_version bump
        4. master portrait was regenerated (reference_image_sha256 differs)
        5. keyframe_image_model swap
        6. validator_version swap
        7. is_identity_master flag on the Asset changed
        """
        params = asset.generation_params or {}
        if not isinstance(params, dict):
            return "generation_params_not_dict"
        if params.get("cache_key_v2") and params.get("cache_key_v2") != expected_cache_key:
            return "cache_key_v2_mismatch"
        if expected_style_fingerprint and params.get("style_fingerprint") != expected_style_fingerprint:
            return "style_fingerprint_changed"
        if expected_contract_version and params.get("identity_contract_version") != expected_contract_version:
            return "identity_contract_version_bumped"
        if expected_model and params.get("model") != expected_model:
            return "model_swapped"
        if expected_validator_version and params.get("identity_validator_version") != expected_validator_version:
            return "validator_version_swapped"
        # 7 — master portrait regenerated: compare reference_image_sha256 of
        # each stored binding against the live binding's hash.
        stored_bindings = params.get("character_bindings") or []
        live_bindings = prompt_data.get("character_bindings") or []
        live_sha_by_char: dict = {}
        for b in live_bindings:
            cid = getattr(b, "character_id", None) if not isinstance(b, dict) else b.get("character_id")
            sha = getattr(b, "reference_image_sha256", None) if not isinstance(b, dict) else b.get("reference_image_sha256")
            if cid and sha:
                live_sha_by_char[cid] = sha
        for sb in stored_bindings:
            if not isinstance(sb, dict):
                continue
            cid = sb.get("character_id")
            live_sha = live_sha_by_char.get(cid)
            if live_sha and sb.get("reference_image_sha256") and sb.get("reference_image_sha256") != live_sha:
                return "master_portrait_regenerated"
        return None

    def _get_story_bible(self, project_id: int) -> Optional[StoryBible]:
        """获取 StoryBible"""
        return self.db.query(StoryBible).filter(
            StoryBible.project_id == project_id
        ).first()

    def _get_chapter_outline(self, project_id: int, chapter_index: int) -> Optional[ChapterOutline]:
        """获取章节大纲"""
        return self.db.query(ChapterOutline).filter(
            ChapterOutline.project_id == project_id,
            ChapterOutline.chapter_index == chapter_index
        ).first()

    def _get_chapter_content(self, project_id: int, chapter_index: int) -> Optional[ChapterContent]:
        """获取章节内容"""
        return self.db.query(ChapterContent).filter(
            ChapterContent.project_id == project_id,
            ChapterContent.chapter_index == chapter_index
        ).first()

    def _build_character_gender_context(self, project_id: int, story_bible: Optional[StoryBible]) -> str:
        """为旧版缺 gender 的角色卡收集上下文。

        Story Bible 早期模板没有显式 gender 字段；章节正文里通常会在角色名附近出现
        “他/她”等代词，可作为兜底推断依据。
        """
        parts: List[str] = []
        if story_bible:
            for value in (
                story_bible.character_relations,
                story_bible.emotional_line,
                story_bible.main_conflict,
            ):
                if value:
                    parts.append(str(value))
            if isinstance(story_bible.raw_json, dict):
                parts.append(str(story_bible.raw_json.get("theme_and_tone") or ""))

        outlines = self.db.query(ChapterOutline).filter(
            ChapterOutline.project_id == project_id
        ).order_by(ChapterOutline.chapter_index.asc()).all()
        for outline in outlines:
            parts.extend([
                str(outline.title or ""),
                str(outline.summary or ""),
                str(outline.conflict or ""),
            ])

        contents = self.db.query(ChapterContent).filter(
            ChapterContent.project_id == project_id
        ).order_by(ChapterContent.chapter_index.asc()).all()
        for content in contents:
            if content.content:
                parts.append(content.content[:3000])

        return "\n".join(p for p in parts if p)

    def _collect_forbidden_characters(
        self,
        outline: Optional[ChapterOutline],
        segments: list,
        story_bible: Optional[StoryBible],
        chapter_content: str,
    ) -> List[str]:
        """
        BG-ENFORCE — 合并多源禁用角色名清单。

        来源（按优先级合并、去重）：
        1. outline.characters — DB 字段（可能为 None / list[str] / list[dict]）
        2. segments[*].characters_present — segmenter LLM 输出
        3. story_bible.characters[*].name — 全局角色表
        4. story_bible.characters[*].name_en / aliases / nicknames — 英文名/别名
        5. 章节正文 chapter_content 中明确出现的 story_bible 角色名（substring 匹配，处理别名遗漏）

        过滤：
        - 空 / 超长 (>30 字) / 含标点的"角色名"（多半是描述误入）
        - 大小写不敏感去重
        - 截断到 20 个（避免 prompt 过长）

        Returns:
            List[str] — 干净的角色名列表
        """
        raw_names: List[str] = []

        # 1) outline.characters
        if outline and outline.characters:
            for c in outline.characters:
                if isinstance(c, str):
                    raw_names.append(c)
                elif isinstance(c, dict):
                    raw_names.append(c.get("name") or "")

        # 2) segment.characters_present
        for seg in (segments or []):
            for c in getattr(seg, "characters_present", None) or []:
                if c:
                    raw_names.append(str(c))

        # 3+4) story_bible.characters（含 name/name_en/aliases/nicknames）
        bible_chars = []
        if story_bible:
            # 优先用顶层 characters 字段，回退到 raw_json.characters
            # getattr 兼容 mock 对象
            chars_source = None
            sb_characters = getattr(story_bible, "characters", None)
            if sb_characters:
                chars_source = sb_characters
            else:
                sb_raw = getattr(story_bible, "raw_json", None)
                if sb_raw and isinstance(sb_raw, dict):
                    chars_source = sb_raw.get("characters")

            for c in chars_source or []:
                if isinstance(c, str):
                    raw_names.append(c)
                    bible_chars.append(c)
                elif isinstance(c, dict):
                    name = (c.get("name") or "").strip()
                    if name:
                        raw_names.append(name)
                        bible_chars.append(name)
                    for k in ("name_en", "english_name"):
                        v = c.get(k)
                        if v and isinstance(v, str):
                            raw_names.append(v.strip())
                    for k in ("aliases", "nicknames"):
                        v = c.get(k)
                        if isinstance(v, list):
                            for alias in v:
                                if alias and isinstance(alias, str):
                                    raw_names.append(alias.strip())

        # 5) 正文中实际出现的 bible 角色名（捕获别名遗漏）
        if chapter_content and bible_chars:
            for name in bible_chars:
                if name and name in chapter_content and name not in raw_names:
                    raw_names.append(name)

        # 清洗：去重 + 过滤脏数据
        seen = set()
        clean = []
        for raw in raw_names:
            if not raw:
                continue
            name = str(raw).strip()
            if not name:
                continue
            # 过滤过长（脏数据，是描述而非名字）
            if len(name) > 30:
                continue
            # 过滤含明显标点的（多半是描述）
            if any(ch in name for ch in [",", ".", "\n", ";", "，", "。", "；", "！", "!"]):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            clean.append(name)

        return clean[:20]

    # ============================================================
    # Stage_Background_AR L4.01-L4.10: schema-driven pipeline
    # ============================================================

    @staticmethod
    def _scene_fingerprint(spec) -> str:
        """L4.03 — scene_fingerprint 5 字段（scene_type + time + weather + mode + physical_state）"""
        env = (spec.environment_description or "").lower()
        physical_state = "default"
        for word, label in [
            ("废墟", "ruins"), ("积水", "flooded"), ("燃烧", "burning"),
            ("积雪", "snowy"), ("荒废", "abandoned"), ("潮湿", "wet"),
        ]:
            if word in env:
                physical_state = label
                break
        weather = (spec.weather or "clear").strip().lower()[:20]
        return "|".join([
            getattr(spec, "scene_selector", "") or getattr(spec, "scene_name", "") or "",
            spec.scene_type,
            spec.time_of_day,
            weather,
            spec.people_policy.mode,
            physical_state,
        ])


    def _dedup_scenes(self, specs: list) -> list:
        """L4.02 — 去重：有 segment_id 时按 id 保活（VNGraph 1:1），否则按 fingerprint。"""
        seen: set = set()
        deduped = []
        for s in specs:
            sid = getattr(s, "segment_id", None)
            if sid is not None:
                fp = f"seg:{int(sid)}"
            else:
                fp = AssetManagementService._scene_fingerprint(s)
            if fp in seen:
                continue
            seen.add(fp)
            deduped.append(s)
        return deduped


    def _persist_background_asset(
        self,
        project_id: int,
        chapter_index: int,
        spec,
        generation_result,
        genre: str = "historical",
        visual_style_profile: Optional[VisualStyleProfile] = None,
        bible: Optional[ProjectVisualBible] = None,
    ) -> Optional[Any]:
        """
        L4.05 — 写入 Asset 表；spec 存进 generation_params JSON。

        generation_params schema_version = "stage_bg_ar_v1"，包含：
        - 18 spec 字段
        - generation metadata（retry_count, fallback_type, validation_results）

        project-visual-bible-v2 §B6 — if ``bible`` is supplied, the 6 stamp
        fields (visual_bible_version / style_fingerprint / scene_visual_seed /
        scene_treatment / validation_skipped / *_version) are merged in.
        """
        from app.models import Asset
        try:
            from datetime import datetime
            from pathlib import Path as _Path
            # 把本地路径转成 URL 形式：static/assets/x.png → /static/assets/x.png
            _raw_path = generation_result.image_path or ""
            if _raw_path and not _raw_path.startswith(("http://", "https://", "/")):
                _image_url = "/" + _raw_path
            else:
                _image_url = _raw_path
            asset = Asset(
                project_id=project_id,
                chapter_index=chapter_index,
                asset_type="background",
                target_name=spec.scene_name,
                prompt=generation_result.final_prompt or "",
                image_url=_image_url,
                status="completed" if generation_result.status == "completed" else "failed",
                genre=genre,
                mood=spec.atmosphere[:60] if spec.atmosphere else None,
                description_cn=spec.environment_description[:300],
            )
            asset.scene_location = spec.scene_name
            # project-visual-bible-v2 §B6 — pop per-scene treatment registered
            # by ``generate_chapter_backgrounds_v2`` when it called classify_scene.
            scene_treatment = _pop_scene_treatment(spec.scene_selector)
            # Stage_Background_Entity_Exclusion — stamp the cache-version +
            # forbidden-entity fingerprint so that any future change to the
            # entity list (add/remove/alias/signature) or to the exclusion
            # text automatically invalidates older Asset rows. The assembler
            # already embeds the same version tag in the prompt text, so the
            # on-disk PNG cache also rolls over when this constant bumps.
            from app.services.background_story_entity_text import (
                BACKGROUND_ENTITY_EXCLUSION_VERSION,
            )
            forbidden_entity_ids = [
                str(e.get("character_id")) for e in (spec.forbidden_entities or [])
                if e.get("character_id")
            ]
            forbidden_entity_names = []
            for e in (spec.forbidden_entities or []):
                name = e.get("canonical_name")
                if name:
                    forbidden_entity_names.append(str(name))
                for alias in (e.get("aliases") or []):
                    if alias:
                        forbidden_entity_names.append(str(alias))
            # Fingerprint: stable hash of sorted entity ids + species + aliases.
            # Used by operators to verify that two assets produced for the
            # same scene obeyed the same exclusion list.
            _fp_seed = sorted(
                f"{e.get('character_id', '')}|{e.get('species', '')}|"
                f"{','.join(sorted([str(a) for a in (e.get('aliases') or []) if a]))}"
                for e in (spec.forbidden_entities or [])
            )
            _fp_hash = hashlib.md5(
                "\n".join(_fp_seed).encode("utf-8")
            ).hexdigest() if _fp_seed else None

            base_params = {
                "schema_version": "stage_bg_ar_v1",
                "project_genre": genre,
                "style_fingerprint": (
                    visual_style_profile.fingerprint if visual_style_profile else None
                ),
                "visual_style_profile": (
                    visual_style_profile.to_dict() if visual_style_profile else None
                ),
                # Stable scene fingerprint shared with legacy graph payloads so the
                # assembler can bind BackGroundNode ↔ Asset by ID instead of by
                # LLM-derived display names.
                "segment_id": getattr(spec, "segment_id", None),
                "segment_location": getattr(spec, "segment_location", None),
                "scene_spec": spec.model_dump(mode="json"),
                "entity_exclusion_version": BACKGROUND_ENTITY_EXCLUSION_VERSION,
                "forbidden_entity_ids": forbidden_entity_ids,
                "forbidden_entity_names": forbidden_entity_names,
                "forbidden_entity_fingerprint": _fp_hash,
                "generation_metadata": {
                    "retry_count": generation_result.retry_count,
                    "fallback_type": generation_result.fallback_type,
                    "validation_results": [
                        v.model_dump() for v in (generation_result.validation_results or [])
                    ],
                    "scene_selector": spec.scene_selector,
                },
                # Stage_VNGraph_Assembly_AR — paragraph 锚点字段提到 top-level，
                # assembler 直接从 generation_params 读，不必反序列化嵌套
                # scene_spec。所有字段都是 Optional，老 spec 不填也不影响落库。
                # source_excerpt 优先取 BackgroundSceneSpec.source_excerpt，
                # 没有则 fallback 到 evidence_spans 首条。
                "source_excerpt": (
                    getattr(spec, "source_excerpt", None)
                    or (spec.evidence_spans[0] if spec.evidence_spans else None)
                ),
                "segment_start_marker": getattr(spec, "segment_start_marker", None),
                "segment_end_marker": getattr(spec, "segment_end_marker", None),
                "scene_fingerprint": getattr(spec, "scene_fingerprint", None),
                "event_signature": getattr(spec, "event_signature", None),
                "scene_selector": spec.scene_selector,
            }
            asset.generation_params = self._stamp_visual_bible_params(
                base_params,
                bible,
                scene_selector=spec.scene_selector,
                project_id=project_id,
                scene_treatment=scene_treatment,
            )
            self.db.add(asset)
            self.db.commit()
            self.db.refresh(asset)
            return asset
        except Exception as e:
            xlog.warn(
                project_id,
                "[asset-mgr] background persist failed project_id=%d chapter_index=%d err=%s",
                project_id, chapter_index, e,
            )
            self.db.rollback()
            return None


    def _persist_background_asset_if_passed(
        self,
        project_id: int,
        chapter_index: int,
        spec,
        generation_result,
        genre: str = "historical",
        visual_style_profile: Optional[VisualStyleProfile] = None,
        bible: Optional[ProjectVisualBible] = None,
    ) -> Optional[Any]:
        """仅将生成完成且通过启用的背景验收的图片写入 Asset 表。

        Stage_Background_Entity_Exclusion — 当 VLM 验收把
        ``generation_result.status`` 置为 ``quarantined`` 时，禁止写 Asset：
        被 quarantine 的背景图既不进入 Asset 表，也不进入 VN manifest。
        调用方拿到 ``asset is None`` 即可判定该场景本次没有可发布产物。
        """
        if generation_result.status != "completed":
            return None
        if not generation_result.image_path:
            return None
        return self._persist_background_asset(
            project_id,
            chapter_index,
            spec,
            generation_result,
            genre=genre,
            visual_style_profile=visual_style_profile,
            bible=bible,
        )


    async def generate_chapter_backgrounds_v2(
        self,
        project_id: int,
        chapter_index: int,
        moods: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        L4.01 — 合并管线（对外唯一章节背景生成入口）：
        1. SceneSegmenter（与 VNGraph 同源）→ 权威段数 + segment_id
        2. analyzer.enrich_segments → 仅为每段补环境描述（不决定张数）
        3. dedup by segment_id / fingerprint
        4. classifier → style_tags per spec
        5. generator → image + bounded validation/retry
        6. persist → only completed Asset with variation_info

        L4.09 — 返回签名：{total, generated, failed, results}
        ``generate_chapter_backgrounds`` 亦转发到本方法。
        """
        from app.services.background_scene_analyzer_service import BackgroundSceneAnalyzerService
        from app.services.image_generation_service import image_generation_service
        from app.services.generation_report import MissingPrerequisiteError, sanitize_detail

        outline = self._get_chapter_outline(project_id, chapter_index)
        story_bible = self._get_story_bible(project_id)
        if not outline:
            raise MissingPrerequisiteError(
                stage="backgrounds",
                code="missing_outline",
                safe_message="章节大纲不存在，无法生成背景",
            )
        if not story_bible:
            raise MissingPrerequisiteError(
                stage="backgrounds",
                code="missing_story_bible",
                safe_message="StoryBible 不存在，无法生成背景",
            )

        # 章节正文
        chapter_content_text = ""
        content_obj = self._get_chapter_content(project_id, chapter_index)
        if content_obj:
            chapter_content_text = content_obj.content or ""

        outline_dict = {
            # scene 为空时用 summary 兜底，与 VNGraph / 旧路径保持一致
            "scene": outline.scene or (outline.summary or "")[:80],
            "summary": outline.summary,
            "characters": outline.characters,
            "conflict": outline.conflict,
            "emotion": outline.emotion,
            "visual_keywords": outline.visual_keywords,
        }
        sb_dict = dict(story_bible.raw_json) if isinstance(story_bible.raw_json, dict) else {}
        # project-visual-bible-v2 §B6 — lock the project bible at generate entry
        bible = self._ensure_visual_bible_locked(project_id, story_bible)
        visual_style_profile = self._build_visual_style_profile(project_id, story_bible)
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if project is not None and getattr(project, "source_work", None):
            sb_dict.setdefault("source_work", project.source_work)

        # Step 1: VNGraph-aligned segments are authoritative for count + ids.
        segments = await scene_segmenter_service.segment_chapter(
            chapter_content_text, outline_dict,
        )
        xlog.info(
            project_id,
            "[asset-mgr] backgrounds_v2 segments project_id=%d chapter_index=%d count=%d locations=%s",
            project_id,
            chapter_index,
            len(segments),
            [getattr(s, "location", "") for s in segments],
        )

        # Step 2: analyzer only enriches; never shrinks/expands the segment list.
        analyzer = BackgroundSceneAnalyzerService()
        specs = await analyzer.enrich_segments(
            segments,
            chapter_index=chapter_index,
            chapter_content=chapter_content_text,
            chapter_outline=outline_dict,
            story_bible=sb_dict,
        )

        # Step 3: dedup (segment_id-preserving)
        specs = self._dedup_scenes(specs)

        # Step 3: project-level style treatment per scene. Under the
        # project-visual-bible-v2 contract, only 4 per-scene fields may vary
        # (camera / atmosphere / accent_palette / time_of_day). Art-style /
        # texture / palette belong to the locked bible and MUST NOT be
        # emitted per-scene (plan §B, hard constraint).
        genre = visual_style_profile.project_genre
        classifier = BackgroundStyleClassifierService()
        for spec in specs:
            try:
                treatment = await classifier.classify_scene(spec, bible)
            except Exception as _cls_err:
                # 分镜级风格调度失败会让该场景退回统一默认调度，属于质量回退，
                # 用 WARN 暴露（原先 INFO 会被当成正常流水账忽略）。
                # 同时显式登记 spec 派生的兜底处理，保留镜头、氛围和时段变化。
                treatment = classifier.fallback_scene_treatment(spec)
                xlog.warn(
                    project_id,
                    "[asset-mgr] classify_scene degraded scene=%s bible=%s "
                    "fallback=spec_fields err=%s",
                    getattr(spec, "scene_selector", "?"),
                    "None" if bible is None else "locked", _cls_err,
                )
            _register_scene_treatment(spec.scene_selector, treatment)
            # Keep spec.style_tags populated for backward compat with the
            # prompt assembler; values come from the visual_style_profile
            # (which is itself derived from the locked bible).
            spec.style_tags = visual_style_profile_service.background_style_tags(
                visual_style_profile,
                spec,
            )
            spec.people_policy.mode = "empty_required"
            existing_rationale = spec.people_policy.rationale or ""
            # 幂等检查：避免 LLM 返回的 rationale 自带"全局背景策略"标记导致重复累加
            if (
                "FORCED_EMPTY_BY_GLOBAL_POLICY" not in existing_rationale
                and "全局背景策略" not in existing_rationale
            ):
                spec.people_policy.rationale = (
                    f"{existing_rationale}；已按全局背景策略强制设为空场".lstrip("；")
                )

        # forbidden_characters（合并 outline + segment + bible + 正文）
        forbidden_characters = self._collect_forbidden_characters(
            outline=outline,
            segments=segments,
            story_bible=story_bible,
            chapter_content=chapter_content_text,
        )

        # Step 4-6: per-spec generate + persist
        results = []
        errors = []
        generated = 0
        failed = 0

        from app.core.config import get_settings
        _parallel_bg = get_settings().parallel_background_generation

        for spec in specs:
            spec.forbidden_characters = forbidden_characters

        if _parallel_bg:
            # === 两段式：阶段 A 并发调供应商（无 DB 写）===
            async def _gen_one_bg(spec):
                gen_result = await image_generation_service.generate_background_with_validation(
                    spec=spec,
                    forbidden_characters=forbidden_characters,
                    genre=genre,
                    visual_style_profile=visual_style_profile.to_dict(),
                )
                return gen_result

            gen_outcomes = await asyncio.gather(
                *[_gen_one_bg(s) for s in specs],
                return_exceptions=True,
            )

            # === 阶段 B：串行持久化 + results 组装（主 session 单协程）===
            for spec, gen_result in zip(specs, gen_outcomes):
                if isinstance(gen_result, Exception):
                    failed += 1
                    results.append({
                        "success": False,
                        "scene_name": spec.scene_name,
                        "scene_selector": spec.scene_selector,
                        "segment_id": spec.segment_id,
                        "status": "failed",
                        "reason": str(gen_result),
                    })
                    errors.append({
                        "code": "background_spec_exception",
                        "message": sanitize_detail(f"{type(gen_result).__name__}: {gen_result}"),
                        "scene": spec.scene_name,
                        "retryable": True,
                    })
                    continue
                try:
                    asset = self._persist_background_asset_if_passed(
                        project_id,
                        chapter_index,
                        spec,
                        gen_result,
                        genre=genre,
                        visual_style_profile=visual_style_profile,
                        bible=bible,
                    )
                    results.append({
                        "success": gen_result.status == "completed" and asset is not None,
                        "scene_name": spec.scene_name,
                        "scene_selector": spec.scene_selector,
                        "segment_id": spec.segment_id,
                        "scene_type": spec.scene_type,
                        "status": gen_result.status,
                        "retry_count": gen_result.retry_count,
                        "fallback_type": gen_result.fallback_type,
                        "asset_id": getattr(asset, "id", None),
                        "image_url": getattr(asset, "image_url", None),
                        "image_path": gen_result.image_path,
                        "reason": gen_result.reason,
                        "validation_results": [
                            v.model_dump() for v in (gen_result.validation_results or [])
                        ],
                    })
                    if gen_result.status == "completed" and asset is not None:
                        generated += 1
                    else:
                        failed += 1
                        errors.append({
                            "code": "image_gen_failed",
                            "message": sanitize_detail(
                                gen_result.reason or "background generation did not complete"
                            ),
                            "scene": spec.scene_name,
                            "retryable": True,
                        })
                except Exception as e:
                    failed += 1
                    results.append({
                        "success": False,
                        "scene_name": spec.scene_name,
                        "scene_selector": spec.scene_selector,
                        "segment_id": spec.segment_id,
                        "status": "failed",
                        "reason": str(e),
                    })
                    errors.append({
                        "code": "background_spec_exception",
                        "message": sanitize_detail(f"{type(e).__name__}: {e}"),
                        "scene": spec.scene_name,
                        "retryable": True,
                    })
        else:
            # === 串行路径（原行为，默认）===
            for spec in specs:
                try:
                    gen_result = await image_generation_service.generate_background_with_validation(
                        spec=spec,
                        forbidden_characters=forbidden_characters,
                        genre=genre,
                        visual_style_profile=visual_style_profile.to_dict(),
                    )
                    asset = self._persist_background_asset_if_passed(
                        project_id,
                        chapter_index,
                        spec,
                        gen_result,
                        genre=genre,
                        visual_style_profile=visual_style_profile,
                        bible=bible,
                    )
                    results.append({
                        "success": gen_result.status == "completed" and asset is not None,
                        "scene_name": spec.scene_name,
                        "scene_selector": spec.scene_selector,
                        "segment_id": spec.segment_id,
                        "scene_type": spec.scene_type,
                        "status": gen_result.status,
                        "retry_count": gen_result.retry_count,
                        "fallback_type": gen_result.fallback_type,
                        "asset_id": getattr(asset, "id", None),
                        "image_url": getattr(asset, "image_url", None),
                        "image_path": gen_result.image_path,
                        "reason": gen_result.reason,
                        "validation_results": [
                            v.model_dump() for v in (gen_result.validation_results or [])
                        ],
                    })
                    if gen_result.status == "completed" and asset is not None:
                        generated += 1
                    else:
                        failed += 1
                        errors.append({
                            "code": "image_gen_failed",
                            "message": sanitize_detail(
                                gen_result.reason or "background generation did not complete"
                            ),
                            "scene": spec.scene_name,
                            "retryable": True,
                        })
                except Exception as e:
                    xlog.warn(
                        project_id,
                        "[asset-mgr] background spec failed project_id=%d chapter_index=%d scene=%s exc=%s: %s",
                        project_id, chapter_index, spec.scene_name, type(e).__name__, e,
                    )
                    failed += 1
                    results.append({
                        "success": False,
                        "scene_name": spec.scene_name,
                        "scene_selector": spec.scene_selector,
                        "segment_id": spec.segment_id,
                        "status": "failed",
                        "reason": str(e),
                    })
                    errors.append({
                        "code": "background_spec_exception",
                        "message": sanitize_detail(f"{type(e).__name__}: {e}"),
                        "scene": spec.scene_name,
                        "retryable": True,
                    })

        return {
            "total": len(specs),
            "generated": generated,
            "failed": failed,
            "results": results,
            "errors": errors,
        }

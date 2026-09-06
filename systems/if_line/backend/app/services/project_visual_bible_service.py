"""Project-level visual bible — single source of truth for art direction.

This module supersedes :class:`VisualStyleProfile` (which stored three divergent
style blocks per project — portrait_prompt_en / background_prompt_zh /
keyframe_prompt_en — and let every chapter's LLM re-decide the medium).

Design contract:

* A :class:`ProjectVisualBible` is **locked** once first computed for a project
  and stored in ``StoryBible.raw_json["project_visual_bible"]``.
* Subsequent chapter / asset generations MUST read the locked bible; chapter
  LLMs cannot rewrite medium / linework / shading / texture / palette.
* Genre (modern / sci-fi / fantasy / historical / ...) decides only setting,
  costume and technology vocabulary — NOT the visual medium.
* The visual medium + full art-direction block is decided by
  :mod:`visual_style_llm_classifier_service` (LLM) when available, falling
  back to the legacy keyword-bucket path on LLM failure or when env-disabled.
  Canonical source franchises (Genshin etc.) are NO LONGER hard-coded —
  every source work is routed through the LLM so art direction adapts to
  the franchise's recognizable visual language.
* Three-Kingdoms public page keeps using its legacy
  :data:`visual_style_contract_service.THREE_KINGDOMS_INK_V2`; this service
  short-circuits to that contract when ``identity == "three-kingdoms"`` so the
  public page does not regress.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.visual_style_contract_service import (
    THREE_KINGDOMS_INK_V2,
    VisualStyleContract,
)
from app.services.source_visual_profile_service import source_visual_profile_service


VERSION = "project-visual-bible-v5"
SCHEMA_KEY = "project_visual_bible"

# Visual medium buckets. Decoupled from narrative genre.
MEDIUM_ANIME_CEL = "anime_cel"
MEDIUM_INK_COLOR = "ink_color_guofeng"
MEDIUM_DIGITAL_PAINTERLY = "digital_painterly"
MEDIUM_CONCEPT_REALISM = "concept_art_realism"

VALID_MEDIUMS = (
    MEDIUM_ANIME_CEL,
    MEDIUM_INK_COLOR,
    MEDIUM_DIGITAL_PAINTERLY,
    MEDIUM_CONCEPT_REALISM,
)


@dataclass(frozen=True)
class ProjectVisualClassification:
    """Structured classification: genre decides setting, NOT medium.

    ``narrative_genre`` ∈ {modern_campus, sci-fi, fantasy, historical_guofeng,
    wuxia, horror} drives setting_period + palette candidates.
    ``visual_medium`` is decided by explicit user keywords, defaulting to
    ``anime_cel`` — a modern sci-fi story is NOT forced into photorealistic
    concept art unless the user asks for it.

    ``llm_decision`` carries the LLM-derived art-direction block (medium +
    art_direction + linework + shading + texture + palette + forbidden) when
    :mod:`visual_style_llm_classifier_service` produced one. ``derive_bible``
    consumes it to override the medium-default template. None when the LLM
    is disabled / failed / env-off — the keyword fallback path is used.
    """

    narrative_genre: str
    setting_period: str
    visual_medium: str
    realism_level: str
    linework_style: str
    palette_family: str
    confidence: float
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    source_work: str = ""
    source_profile_id: str = ""
    source_confidence: float = 0.0
    llm_decision: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectVisualBible:
    """Immutable per-project art-direction contract.

    Fields marked ★ in the plan are part of the fingerprint — once locked they
    cannot be re-derived from chapter text. Role fields (portrait_role /
    background_role / keyframe_role) extend the shared block but do not
    override it.
    """

    version: str
    style_family: str
    medium: str
    art_direction: str
    linework: str
    shading: str
    texture: str
    base_palette: Tuple[str, ...]
    contrast_policy: str
    lighting_policy: str
    palette_policy: str
    forbidden_styles: Tuple[str, ...]
    portrait_role: str
    background_role: str
    keyframe_role: str
    fingerprint: str = ""
    source_work: str = ""
    source_profile_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------
# Default bibles per visual medium. Setting/genre only feeds vocabulary,
# never overrides these.
# ------------------------------------------------------------------

_BASE_FORBIDDEN = (
    "photorealistic rendering",
    "3D game render",
    "glossy plastic skin",
    "oil painting thick strokes",
    "neon cyberpunk palette",
    "inconsistent art style drift",
)

_DEFAULT_ANIME_CEL = ProjectVisualBible(
    version=VERSION,
    style_family="anime_cel",
    medium="transparent_sprite_compatible_2d_anime",
    art_direction=(
        "cohesive 2D anime visual novel illustration, clean cel-shading, "
        "shared between character sprites and environment backgrounds"
    ),
    linework="crisp anime line art, clean cel-shaded surfaces, soft gradients",
    shading="flat base color with two-level shadow, controlled rim light",
    texture="subtle film grain, no paper texture, no photographic noise",
    base_palette=(
        "soft sky blue",
        "warm ivory",
        "muted coral",
        "controlled charcoal",
    ),
    contrast_policy="medium",
    lighting_policy="soft_diffused",
    palette_policy="warm_cool_balanced",
    forbidden_styles=_BASE_FORBIDDEN,
    portrait_role=(
        "ASSET ROLE: PORTRAIT. Transparent full-body character sprite, RGBA "
        "PNG, alpha channel fully transparent, crisp silhouette, full body "
        "visible from head to feet, feet not clipped, head never touches top "
        "edge, signature outfit and accessories preserved."
    ),
    background_role=(
        "ASSET ROLE: BACKGROUND. Empty environment establishing shot, no "
        "humans, no characters, no animals, no bodies, no corpses, no remains, "
        "no character silhouettes, no text, no watermark, 16:9 "
        "widescreen, lower-third visually open for sprite compositing, "
        "palette matches the portrait sprites."
    ),
    keyframe_role=(
        "ASSET ROLE: KEYFRAME. Narrative CG with character(s) in environment, "
        "characters composited on top of the same environment art bible, "
        "action moment emphasized, no text, no watermark."
    ),
)

_DEFAULT_INK_COLOR = ProjectVisualBible(
    version=VERSION,
    style_family="ink_color_guofeng",
    medium="2d_hand_painted_ink_and_color",
    art_direction=(
        "Chinese ink-and-color historical visual novel illustration, shared "
        "between character sprites and environment backgrounds"
    ),
    linework=(
        "restrained confident linework with subtle brush-like lift and fall, "
        "neither thick oil strokes nor hard Japanese cel-shade edges"
    ),
    shading=(
        "ink wash tonal modulation combined with light flat painting, "
        "character silhouettes slightly crisper than the environment"
    ),
    texture="very subtle paper grain across both characters and backgrounds",
    base_palette=(
        "muted earth red",
        "aged bronze",
        "dark jade",
        "warm ivory",
        "ink black",
    ),
    contrast_policy="cinematic",
    lighting_policy="natural_window",
    palette_policy="warm_cool_balanced",
    forbidden_styles=_BASE_FORBIDDEN + (
        "modern clothing",
        "Japanese samurai clothing",
        "generic Japanese galgame style",
        "modern anime school style",
        "chibi proportions",
    ),
    portrait_role=_DEFAULT_ANIME_CEL.portrait_role,
    background_role=_DEFAULT_ANIME_CEL.background_role,
    keyframe_role=_DEFAULT_ANIME_CEL.keyframe_role,
)

_DEFAULT_DIGITAL_PAINTERLY = ProjectVisualBible(
    version=VERSION,
    style_family="digital_painterly",
    medium="2d_digital_painterly_illustration",
    art_direction=(
        "cohesive digital painterly visual novel illustration, soft blended "
        "surfaces, shared between character sprites and environment backgrounds"
    ),
    linework="painterly soft linework, edges defined by value rather than outline",
    shading="smooth digital painting shading, soft gradient transitions",
    texture="subtle canvas texture, no photographic noise",
    base_palette=(
        "dusty rose",
        "deep teal",
        "warm cream",
        "shadow plum",
    ),
    contrast_policy="medium_low",
    lighting_policy="cinematic_directional",
    palette_policy="low_sat_unity",
    forbidden_styles=_BASE_FORBIDDEN,
    portrait_role=_DEFAULT_ANIME_CEL.portrait_role,
    background_role=_DEFAULT_ANIME_CEL.background_role,
    keyframe_role=_DEFAULT_ANIME_CEL.keyframe_role,
)

_DEFAULT_CONCEPT_REALISM = ProjectVisualBible(
    version=VERSION,
    style_family="concept_art_realism",
    medium="digital_concept_art_semi_realistic",
    art_direction=(
        "cohesive concept-art semi-realistic visual novel illustration, "
        "shared between character sprites and environment backgrounds"
    ),
    linework="subtle linework dominated by painterly value blocks",
    shading="realistic form shading, controlled ambient occlusion",
    texture="fine digital painting texture, no photographic noise",
    base_palette=(
        "steel blue",
        "warm grey",
        "muted amber",
        "shadow slate",
    ),
    contrast_policy="cinematic",
    lighting_policy="cinematic_directional",
    palette_policy="low_sat_unity",
    forbidden_styles=_BASE_FORBIDDEN + ("cartoonish anime cel-shading",),
    portrait_role=_DEFAULT_ANIME_CEL.portrait_role,
    background_role=_DEFAULT_ANIME_CEL.background_role,
    keyframe_role=_DEFAULT_ANIME_CEL.keyframe_role,
)

_MEDIUM_DEFAULTS: Dict[str, ProjectVisualBible] = {
    MEDIUM_ANIME_CEL: _DEFAULT_ANIME_CEL,
    MEDIUM_INK_COLOR: _DEFAULT_INK_COLOR,
    MEDIUM_DIGITAL_PAINTERLY: _DEFAULT_DIGITAL_PAINTERLY,
    MEDIUM_CONCEPT_REALISM: _DEFAULT_CONCEPT_REALISM,
}


# ------------------------------------------------------------------
# Keyword buckets for classification
# ------------------------------------------------------------------

_GENRE_BUCKETS: Dict[str, Tuple[str, ...]] = {
    "modern_campus": (
        "现代", "当代", "2020", "大学", "校园", "宿舍", "机房", "计算机",
        "职场", "城市", "ai", "代码", "论文", "毕业", "classroom", "campus",
    ),
    # ``未来`` alone is ordinary narrative language (for example, ``坦然面对未来``)
    # and must not turn a contemporary story into science fiction. Keep only
    # explicit genre terms and future-setting phrases here.
    "sci-fi": (
        "科幻", "赛博", "太空", "机甲", "星际", "近未来", "遥远未来",
        "未来世界", "未来科技", "未来都市", "未来城市", "未来社会", "未来时代",
        "未来文明", "未来人类", "发生在未来", "cyber", "space", "sci-fi", "futuristic",
    ),
    "fantasy": (
        "玄幻", "奇幻", "魔法", "异世界", "神话", "超自然", "超能力", "异能",
        "灵力", "查克拉", "忍术", "血继限界", "尾兽", "fantasy", "magic",
        "supernatural",
    ),
    "historical_guofeng": (
        "古代", "古风", "战国", "唐朝", "宫廷", "hanfu", "guofeng",
    ),
    "wuxia": ("武侠", "仙侠", "江湖", "门派", "wuxia"),
    "horror": ("恐怖", "悬疑", "灵异", "horror", "thriller"),
}

# Equal keyword counts are resolved by semantic specificity, not by the order
# of ``if`` statements.  A fantasy story set in an old era must remain fantasy;
# a single costume token such as "古代袍服" must not beat magic/power-system
# evidence.  Historical remains the fallback only when its own setting evidence
# is stronger than the more specific speculative genres.
_GENRE_TIE_PRIORITY: Dict[str, int] = {
    "wuxia": 60,
    "horror": 50,
    "sci-fi": 40,
    "fantasy": 30,
    "historical_guofeng": 20,
    "modern_campus": 10,
}

# Medium override keywords. Absent → default anime_cel.
_MEDIUM_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    MEDIUM_INK_COLOR: ("水墨", "工笔", "设色", "国画", "ink-and-color", "ink wash"),
    MEDIUM_CONCEPT_REALISM: ("写实", "电影感", "photographic", "cinematic realism", "concept art"),
    MEDIUM_DIGITAL_PAINTERLY: ("厚涂", "painterly", "digital painting", "oil-style digital"),
}

_GENRE_TO_SETTING: Dict[str, str] = {
    # This bucket is also the default for contemporary workplace, family and
    # slice-of-life stories, so its shared setting text must stay venue-neutral.
    "modern_campus": "contemporary East Asian everyday-life setting",
    "sci-fi": "near-future science-fiction setting",
    "fantasy": "fantasy setting with supernatural power systems",
    "historical_guofeng": "Chinese classical historical setting",
    "wuxia": "Chinese martial-arts jianghu setting",
    "horror": "contemporary horror setting",
}


class ProjectVisualBibleService:
    """Build, persist and lock the per-project visual bible."""

    VERSION = VERSION

    def __init__(self) -> None:
        # Process-level lock: raw_json is JSON column, multiple coroutines may
        # race to write the first lock. DB advisory lock is overkill here.
        self._lock = threading.Lock()

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def build_for_project(
        self,
        story_bible: Any,
        *,
        project_id: Optional[int] = None,
        project_title: str = "",
        project_style: str = "",
        source_work: str = "",
        source_context: str = "",
        identity: str = "",
        force_rebuild: bool = False,
    ) -> ProjectVisualBible:
        """Return the locked bible for ``story_bible``, building+locking if absent.

        ``story_bible`` may be a SQLAlchemy ``StoryBible`` row or a plain dict.
        When it is a row, the lock is persisted into ``raw_json`` and committed
        by the caller — this service does not commit (it has no db handle).

        ``identity == "three-kingdoms"`` short-circuits to the legacy
        THREE_KINGDOMS_INK_V2 contract so the public reading page does not
        regress.
        """
        if identity == "three-kingdoms":
            return _from_legacy_contract(THREE_KINGDOMS_INK_V2)

        sb_dict = _story_bible_to_dict(story_bible)
        raw = sb_dict.get("raw_json") if isinstance(sb_dict.get("raw_json"), dict) else {}
        existing = raw.get(SCHEMA_KEY) if isinstance(raw, dict) else None

        if (
            not force_rebuild
            and isinstance(existing, dict)
            and existing.get("schema_version") == VERSION
            and existing.get("bible")
        ):
            return ProjectVisualBible(**existing["bible"])

        with self._lock:
            # Re-check under lock to avoid duplicate builds across coroutines
            raw = sb_dict.get("raw_json") if isinstance(sb_dict.get("raw_json"), dict) else {}
            existing = raw.get(SCHEMA_KEY) if isinstance(raw, dict) else None
            if (
                not force_rebuild
                and isinstance(existing, dict)
                and existing.get("schema_version") == VERSION
                and existing.get("bible")
            ):
                return ProjectVisualBible(**existing["bible"])

            classification = self.infer_classification(
                sb_dict,
                project_title,
                project_style,
                source_work=source_work,
                source_context=source_context,
                _locked_visual_classification=(
                    existing.get("classification")
                    if isinstance(existing, dict)
                    and existing.get("schema_version") == "project-visual-bible-v3"
                    and isinstance(existing.get("classification"), dict)
                    else None
                ),
            )
            bible = self.derive_bible(classification, sb_dict)
            bible = replace(bible, fingerprint=self.fingerprint(bible))

            # Persist into raw_json (caller commits the row)
            if isinstance(story_bible, dict):
                story_bible["raw_json"] = _persist_lock(
                    sb_dict.get("raw_json") or {}, classification, bible,
                )
            else:
                # SQLAlchemy row: write through the ORM-mapped raw_json attr
                story_bible.raw_json = _persist_lock(
                    sb_dict.get("raw_json") or {}, classification, bible,
                )

            return bible

    def infer_classification(
        self,
        story_bible_dict: Any,
        project_title: str = "",
        project_style: str = "",
        *,
        source_work: str = "",
        source_context: str = "",
        _locked_visual_classification: Optional[Dict[str, Any]] = None,
    ) -> ProjectVisualClassification:
        """Structured classification: genre ≠ medium.

        Visual medium + art-direction block is decided by the LLM classifier
        (:mod:`visual_style_llm_classifier_service`) when available. Every
        source work — including canonical franchises like Genshin — is routed
        through the LLM; there is no hardcoded profile bypass. LLM failure /
        env-off → keyword fallback.
        """
        story_bible_dict = _story_bible_to_dict(story_bible_dict)
        raw_for_detection = _classification_raw_payload(
            story_bible_dict.get("raw_json") or {}
        )
        character_signals = _classification_character_signals(
            story_bible_dict.get("characters") or []
        )
        text = " ".join([
            str(project_title or ""),
            str(project_style or ""),
            str(source_work or ""),
            str(source_context or ""),
            str(story_bible_dict.get("source_work") or ""),
            str(story_bible_dict.get("worldview") or ""),
            str(story_bible_dict.get("style_rules") or ""),
            json.dumps(character_signals, ensure_ascii=False),
            json.dumps(raw_for_detection, ensure_ascii=False),
        ]).lower()

        source_detection = source_visual_profile_service.detect(
            explicit_source_work=source_work,
            corpus=text,
        )

        # ---- Step 1: narrative genre (decides setting only) ----
        genre_scores: Dict[str, int] = {}
        genre_evidence: Dict[str, List[str]] = {}
        for genre, keywords in _GENRE_BUCKETS.items():
            hits = [kw for kw in keywords if _keyword_in_text(kw, text)]
            genre_scores[genre] = len(hits)
            genre_evidence[genre] = hits

        # Source-work identity is resolved by the LLM art-direction classifier
        # in Step 2; here genre is inferred purely from keyword signals so the
        # setting_period is always available even when the LLM is offline.
        matched_genres = [
            genre for genre, score in genre_scores.items() if score > 0
        ]
        if matched_genres:
            narrative_genre = max(
                matched_genres,
                key=lambda genre: (
                    genre_scores[genre],
                    _GENRE_TIE_PRIORITY.get(genre, 0),
                ),
            )
            evidence = genre_evidence[narrative_genre]
            confidence = min(0.95, 0.7 + 0.05 * genre_scores[narrative_genre])
        else:
            narrative_genre = "modern_campus"
            evidence = []
            confidence = 0.4

        setting_period = _GENRE_TO_SETTING.get(narrative_genre, _GENRE_TO_SETTING["modern_campus"])

        # ---- Step 2: visual medium + full art-direction block ----
        # Priority:
        #   1. Migrated locked visual (v3 projects) — keep the already locked
        #      medium/art decision so migration cannot spend another LLM call or
        #      unexpectedly restyle an existing project's three asset families.
        #   2. LLM classifier — visual_style_llm_classifier_service produces a
        #      complete VisualStyleDecision; we trust its medium + use its
        #      art_direction/linework/shading/texture/palette as a locked override.
        #   3. Keyword fallback — legacy _MEDIUM_KEYWORDS path; LLM disabled/failed.
        llm_decision: Optional[Dict[str, Any]] = None
        if isinstance(_locked_visual_classification, dict):
            # v3 -> v4 changes genre inference only. Keep the already locked
            # medium/art decision so migration cannot spend another LLM call or
            # unexpectedly restyle an existing project's three asset families.
            previous_decision = _locked_visual_classification.get("llm_decision")
            previous_medium = str(
                _locked_visual_classification.get("visual_medium") or ""
            )
            if isinstance(previous_decision, dict):
                llm_decision = dict(previous_decision)
                medium = str(llm_decision.get("medium") or previous_medium)
            else:
                medium = previous_medium
            if medium not in VALID_MEDIUMS:
                medium = MEDIUM_ANIME_CEL
            medium_evidence = ["migrated_locked_visual:v3"]
        else:
            llm_decision_raw = self._call_llm_classifier(
                story_bible_dict=story_bible_dict,
                project_title=project_title,
                project_style=project_style,
                source_work=source_work or source_detection.source_work,
                source_context=source_context,
            )
            if llm_decision_raw is not None:
                medium = llm_decision_raw.medium
                medium_evidence = [f"llm:{llm_decision_raw.rationale or 'ok'}"]
                llm_decision = llm_decision_raw.to_dict()
            else:
                medium = MEDIUM_ANIME_CEL
                medium_evidence = []
                for candidate, keywords in _MEDIUM_KEYWORDS.items():
                    hits = [kw for kw in keywords if _keyword_in_text(kw, text)]
                    if hits:
                        medium = candidate
                        medium_evidence = hits
                        break

        # ---- Step 3: derived fields ----
        realism_level = {
            MEDIUM_ANIME_CEL: "low",
            MEDIUM_INK_COLOR: "medium",
            MEDIUM_DIGITAL_PAINTERLY: "medium",
            MEDIUM_CONCEPT_REALISM: "high",
        }.get(medium, "low")
        linework_style = {
            MEDIUM_ANIME_CEL: "crisp_cel",
            MEDIUM_INK_COLOR: "brush_ink",
            MEDIUM_DIGITAL_PAINTERLY: "painterly_soft",
            MEDIUM_CONCEPT_REALISM: "painterly_soft",
        }.get(medium, "crisp_cel")
        palette_family = {
            "modern_campus": "bright_youthful",
            "sci-fi": "cool_neon",
            "fantasy": "jewel_tone",
            "historical_guofeng": "warm_classical",
            "wuxia": "warm_classical",
            "horror": "muted_earth",
        }.get(narrative_genre, "bright_youthful")

        return ProjectVisualClassification(
            narrative_genre=narrative_genre,
            setting_period=setting_period,
            visual_medium=medium,
            realism_level=realism_level,
            linework_style=linework_style,
            palette_family=palette_family,
            confidence=confidence,
            evidence=tuple(evidence + medium_evidence),
            source_work=source_detection.source_work,
            source_profile_id="",
            source_confidence=source_detection.confidence,
            llm_decision=llm_decision,
        )

    @staticmethod
    def _call_llm_classifier(
        *,
        story_bible_dict: Dict[str, Any],
        project_title: str,
        project_style: str,
        source_work: str,
        source_context: str,
    ) -> Optional[Any]:
        """Sync wrapper around the async LLM classifier.

        Returns the :class:`VisualStyleDecision` or ``None`` on any failure
        (env disabled, API key missing, LLM timeout, validation error).
        Never raises. Caller treats ``None`` as "use keyword fallback".

        This function is called only during first-time bible lock; subsequent
        ``build_for_project`` calls hit the persisted lock and skip inference.
        """
        # Local import to avoid module-load-time circular dependency and
        # to keep the no-LLM fast path zero-cost.
        from app.services.visual_style_llm_classifier_service import (
            visual_style_llm_classifier_service,
        )

        coro = visual_style_llm_classifier_service.classify(
            project_title=project_title,
            project_style=project_style,
            worldview=str(story_bible_dict.get("worldview") or ""),
            style_rules=str(story_bible_dict.get("style_rules") or ""),
            source_work=source_work,
            source_context=source_context,
            characters=_classification_character_signals(
                story_bible_dict.get("characters") or []
            ),
        )
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is None:
                # No running loop in this thread: safe to create one and
                # run the coroutine to completion.
                return asyncio.new_event_loop().run_until_complete(coro)

            # A loop IS running in this thread (typical: caller is an async
            # FastAPI handler). We cannot run the coro on the same loop
            # without deadlocking. Spawn a worker thread with its OWN event
            # loop and run the coro there. The classifier's HTTP client is
            # thread-safe (PooledAsyncOpenAI creates its own loop internally),
            # and bible lock happens once per project lifetime so the
            # one-time thread spawn cost is acceptable.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=20.0)
        except Exception:
            # LLM classifier is best-effort; never propagate to the bible path.
            return None

    def derive_bible(
        self,
        classification: ProjectVisualClassification,
        story_bible_dict: Dict[str, Any],
    ) -> ProjectVisualBible:
        """Pick the medium-default bible; layer genre vocabulary on top of
        ``art_direction`` only (medium/linework/shading/texture/palette stay
        medium-bound).

        Layer order (highest priority first):
          1. LLM decision (when classification.llm_decision is not None).
          2. Medium-default template keyed by ``classification.visual_medium``.
        """
        if classification.llm_decision is not None:
            # LLM produced a full art-direction block. Use the medium-default
            # template as the base (for role fields / shared framing) then
            # overwrite every style field with the LLM's locked decision.
            decision = classification.llm_decision
            medium_default = _MEDIUM_DEFAULTS.get(
                decision.get("medium") or classification.visual_medium,
                _DEFAULT_ANIME_CEL,
            )
            palette = tuple(decision.get("base_palette") or medium_default.base_palette)
            forbidden = tuple(
                decision.get("forbidden_styles") or medium_default.forbidden_styles
            )
            base = replace(
                medium_default,
                style_family=decision.get("medium") or medium_default.style_family,
                art_direction=decision.get("art_direction") or medium_default.art_direction,
                linework=decision.get("linework") or medium_default.linework,
                shading=decision.get("shading") or medium_default.shading,
                texture=decision.get("texture") or medium_default.texture,
                base_palette=palette,
                contrast_policy=decision.get("contrast_policy") or medium_default.contrast_policy,
                lighting_policy=decision.get("lighting_policy") or medium_default.lighting_policy,
                palette_policy=decision.get("palette_policy") or medium_default.palette_policy,
                forbidden_styles=forbidden,
            )
        else:
            base = _MEDIUM_DEFAULTS.get(classification.visual_medium, _DEFAULT_ANIME_CEL)

        if classification.source_work:
            base = replace(
                base,
                source_work=classification.source_work,
                art_direction=(
                    f"{base.art_direction}. Faithful visual adaptation of the explicitly named "
                    f"source work {classification.source_work}; preserve its recognizable character-design, "
                    "costume, environment, color and material language rather than falling back to generic genre art"
                ),
                forbidden_styles=base.forbidden_styles + (
                    "generic genre art that ignores the named source work",
                ),
            )

        # Inject genre setting vocabulary into art_direction (the ONE field
        # where setting-context is allowed to influence the prompt).
        setting = classification.setting_period
        art_direction = f"{base.art_direction}. Setting context: {setting}."

        return replace(base, art_direction=art_direction)

    def fingerprint(self, bible: ProjectVisualBible) -> str:
        """Stable sha256 of all locked fields (excluding fingerprint itself)."""
        payload = bible.to_dict()
        payload["fingerprint"] = ""
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def is_locked(self, story_bible: Any) -> bool:
        sb_dict = _story_bible_to_dict(story_bible)
        raw = sb_dict.get("raw_json") if isinstance(sb_dict.get("raw_json"), dict) else {}
        if not isinstance(raw, dict):
            return False
        existing = raw.get(SCHEMA_KEY)
        return (
            isinstance(existing, dict)
            and existing.get("schema_version") == VERSION
            and bool(existing.get("locked_at"))
        )

    def assert_bible_locked(self, story_bible: Any) -> None:
        """Raise if the project has no locked bible.

        Called at every asset-generation entry point to guarantee chapter LLMs
        can never invent a medium.
        """
        if not self.is_locked(story_bible):
            raise RuntimeError(
                "ProjectVisualBible is not locked for this project — refusing "
                "to generate assets without a project-level art-direction lock."
            )

    def load_locked(self, story_bible: Any) -> Optional[ProjectVisualBible]:
        sb_dict = _story_bible_to_dict(story_bible)
        raw = sb_dict.get("raw_json") if isinstance(sb_dict.get("raw_json"), dict) else {}
        existing = raw.get(SCHEMA_KEY) if isinstance(raw, dict) else None
        if (
            isinstance(existing, dict)
            and existing.get("schema_version") == VERSION
            and existing.get("bible")
        ):
            return ProjectVisualBible(**existing["bible"])
        return None

    def load_locked_classification(
        self,
        story_bible: Any,
    ) -> Optional[ProjectVisualClassification]:
        """Return the classification paired with the locked visual bible.

        Asset compatibility code must consume this value instead of re-running
        keyword inference.  Otherwise newly enriched character metadata can
        change ``project_genre`` while portrait/background/keyframe prompts keep
        using an older locked art direction.
        """
        sb_dict = _story_bible_to_dict(story_bible)
        raw = sb_dict.get("raw_json") if isinstance(sb_dict.get("raw_json"), dict) else {}
        existing = raw.get(SCHEMA_KEY) if isinstance(raw, dict) else None
        if not (
            isinstance(existing, dict)
            and existing.get("schema_version") == VERSION
            and isinstance(existing.get("classification"), dict)
            and existing.get("bible")
        ):
            return None

        payload = dict(existing["classification"])
        payload["evidence"] = tuple(payload.get("evidence") or ())
        try:
            return ProjectVisualClassification(**payload)
        except (TypeError, ValueError):
            return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _story_bible_to_dict(story_bible: Any) -> Dict[str, Any]:
    if isinstance(story_bible, dict):
        return story_bible
    # SQLAlchemy row: accept both mapped columns and the raw_json attr
    raw = getattr(story_bible, "raw_json", None)
    return {
        "raw_json": raw if isinstance(raw, dict) else {},
        "worldview": getattr(story_bible, "worldview", "") or "",
        "style_rules": getattr(story_bible, "style_rules", "") or "",
        "characters": getattr(story_bible, "characters", None) or [],
    }


def _classification_character_signals(characters: Any) -> List[Dict[str, Any]]:
    """Keep identity signals while excluding generated visual descriptions.

    Character ``visual_profile`` / ``appearance`` / ``outfit`` fields are
    downstream outputs. Feeding them back into project classification creates a
    self-reinforcing loop (for example ``historical_robe`` -> historical world).
    Names and narrative roles remain useful for source-work detection without
    carrying those generated costume/style tokens.
    """
    signals: List[Dict[str, Any]] = []
    if not isinstance(characters, (list, tuple)):
        return signals
    for character in characters:
        if isinstance(character, str):
            name = character.strip()
            if name:
                signals.append({"name": name})
            continue
        if not isinstance(character, dict):
            continue
        signal = {
            key: character.get(key)
            for key in ("name", "role", "canonical_franchise", "canonical_name_official")
            if character.get(key)
        }
        if signal:
            signals.append(signal)
    return signals


def _classification_raw_payload(raw_json: Any) -> Dict[str, Any]:
    """Return narrative raw data without cached or derived visual metadata."""
    if not isinstance(raw_json, dict):
        return {}
    return {
        key: value
        for key, value in raw_json.items()
        if key != SCHEMA_KEY
        and key != "characters"
        and key != "character_visual_schema_version"
        and not str(key).startswith("visual_")
    }


def _keyword_in_text(keyword: str, text: str) -> bool:
    """Match CJK tokens by substring and Latin tokens by word boundaries."""
    normalized = str(keyword or "").strip().lower()
    if not normalized:
        return False
    if re.search(r"[\u3400-\u9fff]", normalized):
        return normalized in text
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
            text,
        )
    )


def _persist_lock(
    raw_json: Dict[str, Any],
    classification: ProjectVisualClassification,
    bible: ProjectVisualBible,
) -> Dict[str, Any]:
    merged = dict(raw_json) if isinstance(raw_json, dict) else {}
    merged[SCHEMA_KEY] = {
        "schema_version": VERSION,
        "classification": classification.to_dict(),
        "bible": bible.to_dict(),
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "lock_reason": "first_generate",
    }
    return merged


def _from_legacy_contract(contract: VisualStyleContract) -> ProjectVisualBible:
    """Adapter so the three-kingdoms legacy contract still works."""
    bible = ProjectVisualBible(
        version=VERSION,
        style_family="ink_color_guofeng",
        medium=contract.medium,
        art_direction=contract.shared_art_direction,
        linework=contract.linework,
        shading=contract.shading,
        texture=contract.texture,
        base_palette=tuple(contract.palette),
        contrast_policy="cinematic",
        lighting_policy="natural_window",
        palette_policy="warm_cool_balanced",
        forbidden_styles=tuple(contract.forbidden),
        portrait_role=contract.portrait_role,
        background_role=contract.background_role,
        # keyframe role was not in the legacy contract; reuse background_role
        # framing with character-in-environment suffix.
        keyframe_role=(
            "ASSET ROLE: KEYFRAME. Narrative CG with character(s) in this "
            "same ink-and-color environment, action moment emphasized, no "
            "text, no watermark."
        ),
    )
    return replace(bible, fingerprint=ProjectVisualBibleService().fingerprint(bible))


project_visual_bible_service = ProjectVisualBibleService()


__all__ = [
    "VERSION",
    "SCHEMA_KEY",
    "MEDIUM_ANIME_CEL",
    "MEDIUM_INK_COLOR",
    "MEDIUM_DIGITAL_PAINTERLY",
    "MEDIUM_CONCEPT_REALISM",
    "VALID_MEDIUMS",
    "ProjectVisualClassification",
    "ProjectVisualBible",
    "ProjectVisualBibleService",
    "project_visual_bible_service",
]

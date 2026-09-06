"""
Background Style Classifier Service (Stage_Background_AR L3.09-L3.16)

输入：单个 BackgroundSceneSpec + genre
输出：List[StyleTag] (3-5 个，覆盖 5 维度中至少 3 个)

职责：
- 从受控风格词表中选择 style_tags
- 失败时 deterministic fallback（按 genre + scene_category）
- 不允许 LLM 新增人物/动作/对白相关 tag
- 不允许 LLM 自造新词（必须命中 taxonomy）

设计哲学：Docs/researches/Stage_Background_AR/00_philosophy.md
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.schemas import BackgroundSceneSpec, StyleTag, StyleCharacterLeakError, StyleCoverageError
from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available
from app.services.text_llm_config import DEFAULT_TEXT_LLM_MODEL, resolve_request_model, text_llm_api_key, text_llm_base_url, text_llm_model

logger = logging.getLogger("bg_style_classifier")

# ----------------------- env -----------------------

CLASSIFIER_ENABLED = os.getenv("BG_STYLE_CLASSIFIER_ENABLED", "true").lower() == "true"
CLASSIFIER_MODEL = text_llm_model("BG_STYLE_CLASSIFIER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
CLASSIFIER_API_KEY = text_llm_api_key("BG_STYLE_CLASSIFIER_API_KEY", "REWRITER_API_KEY")
CLASSIFIER_BASE_URL = text_llm_base_url("BG_STYLE_CLASSIFIER_BASE_URL", "REWRITER_BASE_URL")
CLASSIFIER_TIMEOUT = float(os.getenv("BG_STYLE_CLASSIFIER_TIMEOUT", "3.0"))
CLASSIFIER_TEMPERATURE = float(os.getenv("BG_STYLE_CLASSIFIER_TEMPERATURE", "0.1"))
CLASSIFIER_MAX_TOKENS = int(os.getenv("BG_STYLE_CLASSIFIER_MAX_TOKENS", "1024"))

CACHE_DIR = Path(os.getenv(
    "BG_STYLE_CLASSIFIER_CACHE_DIR",
    str(Path(__file__).parent.parent.parent / ".cache" / "bg_style_classifier")
))

# 维度顺序
STYLE_DIMENSIONS = ["art_style", "color_palette", "lens_or_camera_feel", "texture_or_rendering", "mood"]

# L3.13: 角色/动作/对白 黑名单（用于 _validate_no_character_tags）
FORBIDDEN_TAG_KEYWORDS = [
    # 角色
    "主角", "hero", "heroine", "protagonist", "女主", "男主",
    # 动作
    "战斗", "fight", "attack", "奔跑", "run", "拥抱", "hug", "kiss", "亲吻",
    # 关系
    "恋人", "lover", "couple", "夫妻", "兄弟", "师徒",
    # 对白
    "对白", "dialogue", "说道", "said",
]


@dataclass
class ClassifierStats:
    success: bool
    fallback: bool
    tag_count: int
    elapsed: float
    reason: str = ""


class BackgroundStyleClassifierService:
    """L3.09 — 背景风格分类器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._client = None
        self._stats: List[ClassifierStats] = []
        self._config = config or self._load_default_config()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # --------- config ---------

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
    def taxonomy_version(self) -> str:
        return str(self._config.get("_version", "0"))

    def _taxonomy(self) -> Dict[str, Dict[str, List[str]]]:
        return self._config.get("background_style_taxonomy", {}).get("dimensions", {})

    def _fallback_table(self) -> Dict[str, Any]:
        return self._config.get("background_style_fallback", {})

    # --------- llm client ---------

    @property
    def client(self):
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=CLASSIFIER_API_KEY,
                pool_env=("BG_STYLE_CLASSIFIER_API_KEYS", "REWRITER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=CLASSIFIER_BASE_URL,
            )
        return self._client

    # L3.10: _call_llm (no retries, failure → fallback)
    async def _call_llm(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """single-shot, no retry, no_recovery → fallback"""
        try:
            resp = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=resolve_request_model(CLASSIFIER_MODEL),
                    temperature=CLASSIFIER_TEMPERATURE,
                    max_tokens=CLASSIFIER_MAX_TOKENS,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                ),
                timeout=CLASSIFIER_TIMEOUT,
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as e:
            logger.info("classifier LLM failed: %s (will fallback)", e)
            raise

    # --------- validators ---------

    # L3.11: _validate_coverage
    @staticmethod
    def _validate_coverage(tags: List[StyleTag]) -> Tuple[bool, str]:
        if not (3 <= len(tags) <= 5):
            return False, f"tag count {len(tags)} not in [3,5]"
        dims = [t.dimension for t in tags]
        # 每维度 ≤ 1
        seen_dims: Dict[str, int] = {}
        for d in dims:
            seen_dims[d] = seen_dims.get(d, 0) + 1
        for d, cnt in seen_dims.items():
            if cnt > 1:
                return False, f"dimension {d} duplicated ({cnt})"
        if len(seen_dims) < 3:
            return False, f"only {len(seen_dims)} dimensions covered (need >=3)"
        return True, ""

    # L3.12: _validate_in_taxonomy
    def _validate_in_taxonomy(self, tags: List[StyleTag], genre: str) -> Tuple[bool, str]:
        """按 genre 查词表拒绝"""
        tax = self._taxonomy()
        for t in tags:
            genre_bucket = tax.get(t.dimension, {}).get(genre) or tax.get(t.dimension, {}).get("historical", [])
            if genre_bucket and t.value not in genre_bucket:
                return False, f"tag '{t.value}' (dim={t.dimension}) not in {genre} taxonomy"
        return True, ""

    # L3.13: _validate_no_character_tags
    @staticmethod
    def _validate_no_character_tags(tags: List[StyleTag]) -> Tuple[bool, str]:
        for t in tags:
            v_lower = t.value.lower()
            for kw in FORBIDDEN_TAG_KEYWORDS:
                if kw.lower() in v_lower:
                    return False, f"tag '{t.value}' contains forbidden keyword '{kw}'"
        return True, ""

    # L3.14: _deterministic_fallback
    def _deterministic_fallback(self, scene_type: str, genre: str) -> List[StyleTag]:
        """按 genre default + scene_category 覆盖"""
        fallback = self._fallback_table()
        genre_defaults = fallback.get("by_genre_default", {}).get(genre) or \
                         fallback.get("by_genre_default", {}).get("historical", [])
        category_overrides = fallback.get("by_scene_category", {}).get(scene_type, [])

        # 把 genre default string → StyleTag（按维度顺序映射）
        dim_defaults: Dict[str, str] = {}
        for i, val in enumerate(genre_defaults[:len(STYLE_DIMENSIONS)]):
            dim_defaults[STYLE_DIMENSIONS[i]] = val

        tags: List[StyleTag] = []
        for dim in STYLE_DIMENSIONS:
            if dim in dim_defaults:
                try:
                    tags.append(StyleTag(dimension=dim, value=dim_defaults[dim]))
                except Exception:
                    pass

        # 至少 3 维
        if len(tags) < 3:
            # 补齐
            for dim in STYLE_DIMENSIONS:
                if dim not in {t.dimension for t in tags}:
                    val = dim_defaults.get(dim) or "default"
                    try:
                        tags.append(StyleTag(dimension=dim, value=val))
                    except Exception:
                        pass
                if len(tags) >= 3:
                    break

        return tags[:5]

    # L3.16: _cache_key (taxonomy_version 失效)
    def _cache_key(self, spec: BackgroundSceneSpec, genre: str) -> str:
        h = hashlib.sha256()
        h.update(spec.scene_selector.encode("utf-8"))
        h.update(b"|")
        h.update(spec.environment_description.encode("utf-8"))
        h.update(b"|")
        h.update(genre.encode("utf-8"))
        h.update(b"|")
        h.update(self.taxonomy_version.encode("utf-8"))
        return h.hexdigest()

    def _cache_get(self, key: str) -> Optional[List[StyleTag]]:
        path = CACHE_DIR / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [StyleTag(**d) for d in data]
        except Exception:
            return None

    def _cache_set(self, key: str, tags: List[StyleTag]) -> None:
        path = CACHE_DIR / f"{key}.json"
        try:
            path.write_text(
                json.dumps([t.model_dump() for t in tags], ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning("classifier cache write failed: %s", e)

    # --------- LLM assembly ---------

    def _render_system_prompt(self) -> str:
        cfg = self._config.get("rewriter", {}).get("background_style_classifier", {})
        sys_obj = cfg.get("system_prompt", {})
        parts = []
        if sys_obj.get("identity"):
            parts.append(f"# 身份\n{sys_obj['identity']}")
        if sys_obj.get("instructions"):
            parts.append(f"# 任务\n{sys_obj['instructions']}")
        rules = sys_obj.get("rules") or []
        if rules:
            rule_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))
            parts.append(f"# 硬性规则\n{rule_text}")
        if sys_obj.get("context"):
            parts.append(f"# 背景\n{sys_obj['context']}")
        return "\n\n".join(parts)

    def _render_user_prompt(self, spec: BackgroundSceneSpec, genre: str) -> str:
        tax = self._taxonomy()
        # 渲染受控词表（按 genre）
        allowed_lines = []
        for dim in STYLE_DIMENSIONS:
            bucket = tax.get(dim, {}).get(genre) or tax.get(dim, {}).get("historical", [])
            allowed_lines.append(f"{dim}: {bucket}")
        allowed_block = "\n".join(allowed_lines)

        spec_json = json.dumps(
            {
                "scene_name": spec.scene_name,
                "scene_selector": spec.scene_selector,
                "scene_type": spec.scene_type,
                "environment_description": spec.environment_description,
                "lighting": spec.lighting,
                "atmosphere": spec.atmosphere,
                "weather": spec.weather,
                "time_of_day": spec.time_of_day,
                "camera_shot_type": spec.camera_shot_type,
                "people_policy": spec.people_policy.mode,
            },
            ensure_ascii=False,
            indent=2
        )

        return f"""<AllowedTags>
{allowed_block}
</AllowedTags>

<SceneSpec>
{spec_json}
</SceneSpec>

只输出 JSON: {{"tags": [{{"dimension": "...", "value": "..."}}, ...]}}
3-5 个 tag，覆盖 5 维度中至少 3 个。只从 AllowedTags 选，不自造新词。
"""

    async def _classify_with_llm(self, spec: BackgroundSceneSpec, genre: str) -> List[StyleTag]:
        system_prompt = self._render_system_prompt()
        user_prompt = self._render_user_prompt(spec, genre)
        data = await self._call_llm(system_prompt, user_prompt)
        raw_tags = data.get("tags") or []
        tags: List[StyleTag] = []
        for t in raw_tags:
            try:
                tags.append(StyleTag(**t))
            except Exception as e:
                logger.warning("invalid tag %r: %s", t, e)

        # validate
        ok, msg = self._validate_coverage(tags)
        if not ok:
            raise StyleCoverageError(msg)
        ok, msg = self._validate_no_character_tags(tags)
        if not ok:
            raise StyleCharacterLeakError(msg)
        ok, msg = self._validate_in_taxonomy(tags, genre)
        if not ok:
            logger.warning("taxonomy violation: %s — patching", msg)
            # 把不在词表里的 tag 替换为 deterministic fallback
            return self._deterministic_fallback(spec.scene_type, genre)
        return tags

    # --------- main entry ---------

    async def classify(self, spec: BackgroundSceneSpec, genre: str = "historical", use_cache: bool = True) -> List[StyleTag]:
        """单个 spec → style_tags"""
        if use_cache:
            key = self._cache_key(spec, genre)
            cached = self._cache_get(key)
            if cached is not None:
                return cached

        # disabled / no api key → fallback
        if not CLASSIFIER_ENABLED or not api_key_available(
            CLASSIFIER_API_KEY,
            pool_env=("BG_STYLE_CLASSIFIER_API_KEYS", "REWRITER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            return self._deterministic_fallback(spec.scene_type, genre)

        start = time.time()
        try:
            tags = await self._classify_with_llm(spec, genre)
            self._stats.append(ClassifierStats(True, False, len(tags), time.time() - start))
        except Exception as e:
            logger.info("classifier LLM path failed (%s) — deterministic fallback", e)
            tags = self._deterministic_fallback(spec.scene_type, genre)
            self._stats.append(ClassifierStats(False, True, len(tags), time.time() - start, str(e)))

        if use_cache:
            self._cache_set(key, tags)
        return tags

    # L3.15: classify_many batch 模式 + 串行 fallback
    async def classify_many(
        self,
        specs: List[BackgroundSceneSpec],
        genre: str = "historical",
        use_cache: bool = True,
    ) -> List[List[StyleTag]]:
        """batch 分类（串行，每个失败独立 fallback）"""
        results: List[List[StyleTag]] = []
        for spec in specs:
            try:
                tags = await self.classify(spec, genre=genre, use_cache=use_cache)
            except Exception as e:
                logger.warning("classify_many: spec %s failed (%s) — fallback", spec.scene_selector, e)
                tags = self._deterministic_fallback(spec.scene_type, genre)
            results.append(tags)
        return results

    def stats(self) -> List[ClassifierStats]:
        return list(self._stats)

    # ==================================================================
    # project-visual-bible-v2: classify_scene
    # ==================================================================

    async def classify_scene(
        self,
        spec: BackgroundSceneSpec,
        bible: Optional["ProjectVisualBible"],
    ) -> "SceneVisualTreatment":
        """Return ONLY the per-scene variation fields. The locked bible keeps
        art_style / medium / linework / shading / texture / palette.

        This method is the v2 replacement for :meth:`classify`. The legacy
        method is kept for the existing golden tests; new code paths
        (``generate_chapter_backgrounds_v2`` etc.) should call this instead.

        Per-scene allowed fields (from plan §B5 / §E5):
        * ``camera`` — derived from ``spec.camera_shot_type``
        * ``atmosphere`` — derived from ``spec.atmosphere`` / ``spec.mood``
        * ``time_of_day`` — derived from ``spec.time_of_day``
        * ``accent_palette`` — CONSTRAINED subset of ``bible.base_palette``
          (max 2 colors, must appear in bible palette)

        ``art_style`` / ``color_palette`` / ``texture_or_rendering`` are
        deliberately NOT returned — they belong to the locked bible.
        """
        accent_palette = self._derive_accent_palette(
            getattr(spec, "scene_selector", "") or "",
            bible,
        )
        return self._scene_treatment_from_spec(spec, accent_palette=accent_palette)

    @staticmethod
    def _scene_treatment_from_spec(
        spec: BackgroundSceneSpec,
        *,
        accent_palette: Tuple[str, ...] = (),
    ) -> "SceneVisualTreatment":
        """Build the non-LLM scene variation fields from the scene spec."""
        # Import locally to avoid a circular import at module load time.
        from app.services.asset_prompt_builder_service import SceneVisualTreatment

        camera_map = {
            "establishing_wide": "24mm wide establishing shot",
            "environment_medium": "35mm environment medium shot",
            "high_angle_distant": "high-angle distant shot",
            "interior_wide": "interior wide shot",
            "telephoto_compressed": "telephoto compressed distant",
            "low_angle_perspective": "low-angle perspective shot",
        }
        shot = getattr(spec, "camera_shot_type", "") or ""
        camera = camera_map.get(shot, "35mm environment establishing shot")

        atmosphere_parts = []
        for attr in ("atmosphere", "mood"):
            val = getattr(spec, attr, "") or ""
            if val:
                atmosphere_parts.append(str(val))
        atmosphere = "; ".join(atmosphere_parts) if atmosphere_parts else "neutral ambient"

        tod = getattr(spec, "time_of_day", "") or ""
        time_of_day = str(tod) if tod else ""

        return SceneVisualTreatment(
            camera=camera,
            atmosphere=atmosphere,
            accent_palette=accent_palette,
            time_of_day=time_of_day,
        )

    def fallback_scene_treatment(
        self,
        spec: BackgroundSceneSpec,
    ) -> "SceneVisualTreatment":
        """Preserve spec-derived variation when bible/classification degrades."""
        return self._scene_treatment_from_spec(spec)

    @staticmethod
    def _derive_accent_palette(
        scene_selector: str, bible: Optional["ProjectVisualBible"],
    ) -> Tuple[str, ...]:
        """Deterministic 2-color subset of ``bible.base_palette``.

        Same scene → same accent pair. Different scenes → different pair (within
        the locked palette). Never invents colors outside the bible.

        ``bible`` 可能为 None（上游 ``_ensure_visual_bible_locked`` 在 bible 锁定
        失败时返回 None）。此时点缀色无法派生，返回空元组——其余三个分镜字段
        （camera / atmosphere / time_of_day）只依赖 ``spec``，仍由 ``classify_scene``
        正常产出。不要让 None bible 把整段分镜调度拖垮。
        """
        if bible is None:
            return ()
        base = list(getattr(bible, "base_palette", None) or [])
        if not base:
            return ()
        if len(base) == 1:
            return (base[0],)
        # Hash scene_selector → 2 distinct indices
        h = hashlib.md5(scene_selector.encode("utf-8")).hexdigest()
        idx1 = int(h[:8], 16) % len(base)
        idx2 = int(h[8:16], 16) % len(base)
        if idx2 == idx1:
            idx2 = (idx1 + 1) % len(base)
        return (base[idx1], base[idx2])

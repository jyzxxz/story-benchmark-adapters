from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import logging
import math
import json
import re
from time import perf_counter
from typing import Any, Iterable
from uuid import UUID

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.asset_service import create_asset_binding, request_asset_variant
from app.application.branch_service import get_owned_session
from app.application.hashing import content_hash
from app.application.public_release_service import get_active_public_release
from app.application.storage_service import (
    get_local_storage_backend,
    register_stored_file,
)
from app.application.task_service import (
    append_task_event,
    complete_task,
    create_generation_task,
    request_cancel,
)
from app.application.vn_graph_derivation import (
    VNGraphDerivationError,
    replay_asset_action_patch,
    validate_derived_vn_graph,
)
from app.application.vn_graph_manifest import (
    asset_version_reference,
    validate_asset_version_reference,
)
from app.core.errors import AppError
from app.integrations.llm.continuation_adapter import (
    CONTINUATION_PROMPT_VERSION,
    ContinuationLLMRequest,
    DirectionSuggestionLLMRequest,
)
from app.library_tags import (
    FACET_CATEGORIES,
    FacetTagSpec,
    MAX_SELECTED_TAGS,
    normalize_required_facets,
)
from app.models import Asset, Project
from app.models_v2 import (
    AssetAction,
    AssetVersion,
    GenerationTask,
    LibraryAsset,
    LibraryAssetTag,
    LibraryAssetEmbedding,
    LibraryTag,
    ReadingContinuation,
    ReadingSession,
    SceneManifest,
    ScriptResourceSlot,
    StateSnapshot,
    StorageObject,
    StoryNode,
    VNGraphHead,
    VNGraphRevision,
    new_uuid,
    utcnow,
)
from app.schemas_continuation_generation import GeneratedContinuation


VISUAL_TYPES = {"portrait", "background", "keyframe"}
logger = logging.getLogger(__name__)
SOURCE_KINDS = {
    "story_bible_revision",
    "outline_revision",
    "chapter_revision",
    "chapter_script_revision",
    "vn_graph_revision",
    "branch_candidate",
    "reading_continuation",
}
UPLOAD_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}
MATCHER_DIMENSIONS = 256
MATCHER_VERSION = "hash-ngram-v1"
MAX_IMAGE_SIDE = 8192
MIN_IMAGE_SIDE = 64
MAX_IMAGE_PIXELS = 40_000_000
CONTINUATION_TASK_KIND = "reading.continuation.generate"
ROLE_TO_ASSET_SLOT = {
    "background": "BackgroundImage",
    "portrait": "TachiIamge",
    "keyframe": "IllustrationImage",
}
ASSET_SLOT_TO_ROLE = {value: key for key, value in ROLE_TO_ASSET_SLOT.items()}
VNGRAPH_NODE_ASSET_SLOT = {
    (2, 3): ("BackgroundImage", "background"),
    (1, 14): ("BackgroundImage", "background"),
    (2, 1): ("TachiIamge", "portrait"),
    (2, 26): ("TachiIamge", "portrait"),
    (2, 6): ("IllustrationImage", "keyframe"),
}
CONTINUATION_EVENT = "reading.continuation.generate.requested"
CONTINUATION_CREDIT_COST = Decimal("3")


# The frozen catalog currently derives its English metadata from stable keys.
# Expand common Chinese visual directions into those catalog terms before the
# hash matcher runs.  Longer phrases are checked first so, for example,
# ``雨夜`` contributes both ``rain`` and ``night`` rather than only ``rain``.
_ZH_VISUAL_QUERY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("赛博朋克", ("cyberpunk",)),
    ("废弃仓库", ("abandoned", "warehouse")),
    ("城市街道", ("city", "street")),
    ("专注思考", ("focused", "thinking")),
    ("古代", ("historical",)),
    ("仙侠", ("xianxia",)),
    ("科幻", ("scifi",)),
    ("现代", ("modern",)),
    ("雨夜", ("rain", "night")),
    ("夜晚", ("night",)),
    ("白天", ("day",)),
    ("黄昏", ("dusk",)),
    ("清晨", ("morning",)),
    ("城市", ("city",)),
    ("街道", ("street",)),
    ("小巷", ("alley",)),
    ("仓库", ("warehouse",)),
    ("学校", ("school",)),
    ("医院", ("hospital",)),
    ("森林", ("forest",)),
    ("海边", ("seaside",)),
    ("房间", ("room",)),
    ("黑客", ("hacker",)),
    ("公主", ("princess",)),
    ("侦探", ("detective",)),
    ("开心", ("happy", "smile")),
    ("微笑", ("happy", "smile")),
    ("悲伤", ("sad",)),
    ("难过", ("sad",)),
    ("生气", ("angry",)),
    ("愤怒", ("angry",)),
    ("严肃", ("serious",)),
    ("专注", ("focused",)),
    ("思考", ("thinking", "focused")),
    ("站立", ("standing",)),
    ("坐着", ("sitting",)),
    ("废弃", ("abandoned",)),
    ("下雨", ("rain",)),
    ("雨", ("rain",)),
    ("夜", ("night",)),
)


def _normalize_asset_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    aliases = {"character": "portrait", "illustration": "keyframe"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in VISUAL_TYPES:
        raise HTTPException(status_code=422, detail="asset_type 仅支持 portrait/background/keyframe")
    return normalized


def _media_url(storage_object_id: str) -> str:
    return f"/api/media/{storage_object_id}"


def _request_hash(value: Any) -> str:
    return content_hash(value)


def _validate_idempotency_key(value: str) -> str:
    key = (value or "").strip()
    if not key or len(key) > 255:
        raise HTTPException(status_code=400, detail="缺少有效的 Idempotency-Key")
    return key


def _terms(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    result = set(re.findall(r"[a-z0-9_\-]+", text))
    chinese = re.findall(r"[\u3400-\u9fff]", text)
    result.update(chinese)
    result.update("".join(chinese[index : index + 2]) for index in range(len(chinese) - 1))
    return {item for item in result if item}


def _expand_visual_query(value: str) -> str:
    """Translate known Chinese visual concepts into frozen-catalog terms."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    expanded: list[str] = []
    consumed = text
    for phrase, terms in _ZH_VISUAL_QUERY_TERMS:
        if phrase not in consumed:
            continue
        expanded.extend(terms)
        consumed = consumed.replace(phrase, " ")
    if not expanded:
        return text
    # Preserve explicit Latin catalog terms supplied alongside Chinese text,
    # while excluding unmatched Han characters that would dilute the hash
    # vector for the current English-only frozen catalog.
    expanded.extend(re.findall(r"[a-z0-9_\-]+", text))
    return " ".join(dict.fromkeys(expanded))


def build_hash_embedding(text: str, *, dimensions: int = MATCHER_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    counts = Counter(_terms(text))
    for term, count in counts.items():
        digest = sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = -1.0 if digest[4] & 1 else 1.0
        vector[index] += sign * (1.0 + math.log(float(count)))
    norm = math.sqrt(sum(item * item for item in vector))
    if norm:
        vector = [item / norm for item in vector]
    return vector


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left)
    right_values = list(right)
    if not left_values or len(left_values) != len(right_values):
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left_values, right_values))))


def _flatten_strings(value: Any) -> list[str]:
    result: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)
        elif item is not None:
            result.append(str(item))
    return result


_BACKGROUND_ENVIRONMENT_TAXONOMY_LABELS: dict[str, str] = {
    "location": "地点",
    "place": "地点",
    "setting": "地点",
    "environment": "环境",
    "time": "时间",
    "time_of_day": "时段",
    "season": "季节",
    "weather": "天气",
    "lighting": "光线",
    "light": "光线",
    "atmosphere": "氛围",
    "architecture": "建筑",
    "terrain": "地形",
    "key_objects": "关键物件",
    "objects": "物件",
    "props": "物件",
    "地点": "地点",
    "环境": "环境",
    "时间": "时间",
    "时段": "时段",
    "季节": "季节",
    "天气": "天气",
    "光线": "光线",
    "氛围": "氛围",
    "建筑": "建筑",
    "地形": "地形",
    "关键物件": "关键物件",
    "物件": "物件",
    "道具": "物件",
}
_BACKGROUND_HUMAN_CLAUSE_RE = re.compile(
    r"(?:"
    r"人物|人群|人脸|人形|角色|主角|众人|路人|行人|百姓|群众|"
    r"士兵|军士|将士|军队|守卫|卫兵|侍卫|侍从|侍女|官员|追兵|伏兵|骑兵|步兵|"
    r"男人|女人|男子|女子|少年|少女|老人|孩童|孩子|"
    r"[一二两三四五六七八九十]+人|身影|人影|背影|剪影|面孔|"
    r"\b(?:person|people|human|character|protagonist|hero|heroine|actor|"
    r"man|woman|boy|girl|child|soldier|troop|guard|attendant|servant|"
    r"crowd|figure|silhouette|face|portrait)\b"
    r")",
    re.IGNORECASE,
)
_BACKGROUND_CHARACTER_ACTION_RE = re.compile(
    r"(?:"
    r"说道|说着|问道|答道|喊道|高喊|低语|对话|"
    r"拦住|交给|递给|率兵|赶到|发现|警惕|愤怒|拔剑|挥剑|持剑|手持|"
    r"围桌|站在|坐在|跪在|走进|走入|冲入|退入|翻身|下马|骑马|"
    r"交战|战斗|追赶|逃跑|奔跑|商议|查看|望向|看着|怒视|拥抱|哭泣|"
    r"\b(?:says?|speaks?|asks?|answers?|shouts?|whispers?|"
    r"stands?|sits?|kneels?|walks?|runs?|rides?|holds?|fights?|"
    r"looks?|watches?|enters?|leaves?|embraces?)\b"
    r")",
    re.IGNORECASE,
)


def _safe_background_environment_fragments(value: Any, *, limit: int = 8) -> list[str]:
    """Keep environment-only prompt fragments and discard character clauses.

    Search queries may contain the complete plot because that improves semantic
    matching. Image-generation prompts must not: positive mentions such as
    "soldiers in the courtyard" can override a later negative prompt. Splitting
    first preserves safe weather, lighting and prop clauses from an otherwise
    mixed scene description.
    """

    fragments: list[str] = []
    for raw in _flatten_strings(value):
        for candidate in re.split(r"[\n\r。！？!?；;，,]+", raw):
            text = re.sub(r"\s+", " ", candidate).strip(" ：:、|-")
            if not text or len(text) > 240:
                continue
            if _BACKGROUND_HUMAN_CLAUSE_RE.search(text):
                continue
            if _BACKGROUND_CHARACTER_ACTION_RE.search(text):
                continue
            if text not in fragments:
                fragments.append(text)
            if len(fragments) >= limit:
                return fragments
    return fragments


def _build_empty_environment_generation_prompt(
    *,
    background: str,
    taxonomy: dict[str, Any] | None,
    style: str | None,
) -> str:
    """Build a character-free positive prompt for a VN background layer."""

    details = _safe_background_environment_fragments(background, limit=10)
    taxonomy_lines: list[str] = []
    for raw_key, raw_value in (taxonomy or {}).items():
        key = str(raw_key or "").strip()
        label = _BACKGROUND_ENVIRONMENT_TAXONOMY_LABELS.get(
            key,
            _BACKGROUND_ENVIRONMENT_TAXONOMY_LABELS.get(key.lower()),
        )
        if not label:
            # Camera/composition fields often repeat "three people around a
            # table". They belong to sprite staging, not the background layer.
            continue
        fragments = _safe_background_environment_fragments(raw_value, limit=3)
        if fragments:
            taxonomy_lines.append(f"{label}：{'、'.join(fragments)}")

    style_fragments = _safe_background_environment_fragments(style, limit=2)
    prompt_parts = [
        "视觉小说纯环境背景层，使用宽幅地点建立镜头。",
        "画面主体只由建筑、景观、地形、静态道具、天气、光线、材质与事件留下的环境痕迹构成。",
        *([f"环境细节：{'；'.join(details)}。"] if details else []),
        *([f"{line}。" for line in taxonomy_lines] if taxonomy_lines else []),
        *([f"统一画风：{'；'.join(style_fragments)}。"] if style_fragments else []),
        "所有空间保持空置，前景留出独立角色图层的叠加区域。",
        "门匾、旗帜、纸张和标牌保持无字或不进入镜头。",
    ]
    return "\n".join(prompt_parts)


def _confidence(score: float) -> str:
    if score >= 0.78:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _recent_exact_match_penalty(candidate: str | None, recent_values: Iterable[str] | None) -> float:
    """Softly down-rank an exact file/catalog repeat without forbidding reuse.

    ``recent_values`` is ordered oldest-to-newest. A repeat in the immediately
    previous visual moment matters most; older uses fade quickly. The cap keeps
    a uniquely correct semantic match available instead of forcing a wrong
    image merely to look different.
    """

    normalized_candidate = str(candidate or "").strip()
    if not normalized_candidate or not recent_values:
        return 0.0
    recent = [str(value or "").strip() for value in recent_values if str(value or "").strip()]
    penalty = 0.0
    for distance, value in enumerate(reversed(recent[-8:])):
        if value != normalized_candidate:
            continue
        penalty += 0.14 * (0.72**distance)
    return min(0.30, penalty)


def _asset_text(asset: LibraryAsset) -> str:
    return " ".join(
        [
            asset.description_cn or "",
            asset.description_en or "",
            *[str(item) for item in (asset.tags or [])],
            *_flatten_strings(asset.taxonomy or {}),
            asset.identity_group or "",
            asset.expression or "",
            asset.pose or "",
            asset.style or "",
        ]
    )


def _embedding_for_asset(db: Session, asset: LibraryAsset, model_version: str) -> list[float]:
    row = (
        db.query(LibraryAssetEmbedding)
        .filter(
            LibraryAssetEmbedding.library_asset_id == asset.id,
            LibraryAssetEmbedding.model_version == model_version,
        )
        .first()
    )
    if row and row.dimensions == len(row.embedding or []) and row.dimensions > 0:
        return [float(item) for item in row.embedding]
    return build_hash_embedding(_asset_text(asset))


def library_asset_dict(asset: LibraryAsset, storage: StorageObject, **extra: Any) -> dict[str, Any]:
    taxonomy = dict(asset.taxonomy or {})
    return {
        "id": asset.id,
        "catalog_version": asset.catalog_version,
        "stable_key": asset.stable_key,
        "asset_type": asset.asset_type,
        "storage_object_id": asset.storage_object_id,
        "media_url": _media_url(asset.storage_object_id),
        "sha256": storage.sha256,
        "description_cn": asset.description_cn or "",
        "description_en": asset.description_en or "",
        "tags": list(asset.tags or []),
        "taxonomy": taxonomy,
        "identity_group": asset.identity_group,
        "expression": asset.expression,
        "pose": asset.pose,
        "style": asset.style,
        "width": taxonomy.get("width"),
        "height": taxonomy.get("height"),
        "has_alpha": taxonomy.get("has_alpha"),
        "quality_score": float(asset.quality_score or 0.0),
        **extra,
    }


def parse_library_tag_ids(raw: str | None) -> list[str]:
    """Parse the public comma-separated tag selector into canonical UUIDs."""

    if raw is None or not raw.strip():
        return []
    tokens = raw.split(",")
    if any(not token.strip() for token in tokens):
        raise HTTPException(status_code=422, detail="tag_ids contains an empty value")
    normalized: list[str] = []
    for token in tokens:
        try:
            tag_id = str(UUID(token.strip()))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(status_code=422, detail="tag_ids must contain UUID values") from exc
        if tag_id not in normalized:
            normalized.append(tag_id)
    if len(normalized) > MAX_SELECTED_TAGS:
        raise HTTPException(
            status_code=422,
            detail=f"tag_ids supports at most {MAX_SELECTED_TAGS} unique values",
        )
    return normalized


def _eligible_library_conditions(
    *,
    catalog_version: str,
    asset_type: str | None,
) -> list[Any]:
    conditions: list[Any] = [
        LibraryAsset.catalog_version == catalog_version,
        LibraryAsset.enabled.is_(True),
        LibraryAsset.safety_status == "approved",
        StorageObject.status == "active",
        StorageObject.deleted_at.is_(None),
    ]
    if asset_type:
        conditions.append(LibraryAsset.asset_type == asset_type)
    return conditions


def _validate_library_tag_ids(
    db: Session,
    *,
    catalog_version: str,
    asset_type: str | None,
    tag_ids: Iterable[str] | None,
) -> dict[str, LibraryTag]:
    normalized: list[str] = []
    for raw_id in tag_ids or ():
        try:
            tag_id = str(UUID(str(raw_id).strip()))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(status_code=422, detail="tag_ids must contain UUID values") from exc
        if tag_id not in normalized:
            normalized.append(tag_id)
    if len(normalized) > MAX_SELECTED_TAGS:
        raise HTTPException(
            status_code=422,
            detail=f"tag_ids supports at most {MAX_SELECTED_TAGS} unique values",
        )
    if not normalized:
        return {}
    rows = (
        db.query(LibraryTag)
        .join(LibraryAssetTag, LibraryAssetTag.tag_id == LibraryTag.id)
        .join(LibraryAsset, LibraryAsset.id == LibraryAssetTag.library_asset_id)
        .join(StorageObject, StorageObject.id == LibraryAsset.storage_object_id)
        .filter(
            *_eligible_library_conditions(
                catalog_version=catalog_version,
                asset_type=asset_type,
            ),
            LibraryTag.id.in_(normalized),
        )
        .distinct()
        .all()
    )
    found = {tag.id: tag for tag in rows}
    missing = [tag_id for tag_id in normalized if tag_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="tag_ids contains values outside the current catalog and asset_type",
        )
    return {tag_id: found[tag_id] for tag_id in normalized}


def _resolve_required_facets(
    db: Session,
    *,
    catalog_version: str,
    asset_type: str | None,
    required_facets: dict[str, Any] | None,
) -> tuple[list[FacetTagSpec], list[LibraryTag], bool]:
    try:
        specs = normalize_required_facets(required_facets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resolved: list[LibraryTag] = []
    for spec in specs:
        tag = (
            db.query(LibraryTag)
            .join(LibraryAssetTag, LibraryAssetTag.tag_id == LibraryTag.id)
            .join(LibraryAsset, LibraryAsset.id == LibraryAssetTag.library_asset_id)
            .join(StorageObject, StorageObject.id == LibraryAsset.storage_object_id)
            .filter(
                *_eligible_library_conditions(
                    catalog_version=catalog_version,
                    asset_type=asset_type,
                ),
                LibraryTag.category == spec.category,
                LibraryTag.value == spec.value,
            )
            .first()
        )
        if tag is None:
            return specs, [], True
        resolved.append(tag)
    return specs, resolved, False


def _library_candidate_query(
    db: Session,
    *,
    catalog_version: str,
    asset_type: str | None,
    identity_group: str | None,
    selected_tag_ids: list[str],
):
    candidate_query = (
        db.query(LibraryAsset, StorageObject)
        .join(StorageObject, StorageObject.id == LibraryAsset.storage_object_id)
        .filter(
            *_eligible_library_conditions(
                catalog_version=catalog_version,
                asset_type=asset_type,
            )
        )
    )
    if identity_group:
        candidate_query = candidate_query.filter(
            LibraryAsset.identity_group == identity_group
        )
    if selected_tag_ids:
        tagged_assets = (
            select(LibraryAssetTag.library_asset_id.label("library_asset_id"))
            .where(LibraryAssetTag.tag_id.in_(selected_tag_ids))
            .group_by(LibraryAssetTag.library_asset_id)
            .having(
                func.count(func.distinct(LibraryAssetTag.tag_id))
                == len(selected_tag_ids)
            )
            .subquery()
        )
        candidate_query = candidate_query.join(
            tagged_assets,
            tagged_assets.c.library_asset_id == LibraryAsset.id,
        )
    return candidate_query


def _log_library_query(
    *,
    operation: str,
    started_at: float,
    catalog_version: str,
    asset_type: str | None,
    selected_tag_count: int,
    candidate_count: int,
    legacy_filter_used: bool,
    query_present: bool,
) -> None:
    logger.info(
        "library_asset_%s catalog_version=%s asset_type=%s selected_tag_count=%s "
        "candidate_count=%s duration_ms=%.3f legacy_filter_used=%s query_present=%s",
        operation,
        catalog_version,
        asset_type or "all",
        selected_tag_count,
        candidate_count,
        (perf_counter() - started_at) * 1000,
        legacy_filter_used,
        query_present,
    )


def search_library_assets(
    db: Session,
    *,
    catalog_version: str,
    matcher_version: str,
    query: str | None,
    asset_type: str | None,
    identity_group: str | None,
    limit: int,
    offset: int,
    required_tag_ids: Iterable[str] | None = None,
    required_facets: dict[str, Any] | None = None,
    preferred_style: str | None = None,
    recent_library_asset_ids: Iterable[str] | None = None,
    recent_storage_object_ids: Iterable[str] | None = None,
    # Transitional service compatibility. HTTP callers must translate legacy
    # fields into required_facets so public ``style`` is a hard constraint.
    style: str | None = None,
    filters: dict[str, Any] | None = None,
    legacy_filter_used: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    started_at = perf_counter()
    normalized_type = _normalize_asset_type(asset_type) if asset_type else None
    if preferred_style is None:
        preferred_style = style
    if required_facets is None:
        required_facets = {
            key: value
            for key, value in (filters or {}).items()
            if key in FACET_CATEGORIES
        }
    explicit_tags = _validate_library_tag_ids(
        db,
        catalog_version=catalog_version,
        asset_type=normalized_type,
        tag_ids=required_tag_ids,
    )
    facet_specs, facet_tags, missing_facet = _resolve_required_facets(
        db,
        catalog_version=catalog_version,
        asset_type=normalized_type,
        required_facets=required_facets,
    )
    selected_tags = dict(explicit_tags)
    selected_tags.update({tag.id: tag for tag in facet_tags})
    selected_tag_ids = list(selected_tags)
    query_text = str(query or "").strip()
    legacy_filter_used = legacy_filter_used or bool(filters)
    if missing_facet:
        _log_library_query(
            operation="search",
            started_at=started_at,
            catalog_version=catalog_version,
            asset_type=normalized_type,
            selected_tag_count=len(selected_tag_ids),
            candidate_count=0,
            legacy_filter_used=legacy_filter_used,
            query_present=bool(query_text),
        )
        return [], 0

    candidate_query = _library_candidate_query(
        db,
        catalog_version=catalog_version,
        asset_type=normalized_type,
        identity_group=identity_group,
        selected_tag_ids=selected_tag_ids,
    )
    recent_asset_ids = list(recent_library_asset_ids or ())
    recent_storage_ids = list(recent_storage_object_ids or ())
    fast_sql_page = not query_text and not preferred_style and not recent_asset_ids and not recent_storage_ids
    if fast_sql_page:
        total = candidate_query.count()
        rows = (
            candidate_query.order_by(
                LibraryAsset.quality_score.desc(),
                LibraryAsset.stable_key.desc(),
                LibraryAsset.id.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        items: list[dict[str, Any]] = []
        for asset, storage in rows:
            quality_score = float(asset.quality_score or 0.0)
            base_score = 0.45 + 0.10 * quality_score
            confidence = _confidence(base_score)
            items.append(
                library_asset_dict(
                    asset,
                    storage,
                    score=round(base_score, 6),
                    confidence=confidence,
                    score_breakdown={
                        "semantic": 0.0,
                        "taxonomy": 1.0,
                        "style": 1.0,
                        "quality": round(quality_score, 6),
                        "base_score": round(base_score, 6),
                        "repeat_penalty": 0.0,
                    },
                    conflicts=[],
                    fallback_reason="low_match_score" if confidence == "low" else None,
                )
            )
        _log_library_query(
            operation="search",
            started_at=started_at,
            catalog_version=catalog_version,
            asset_type=normalized_type,
            selected_tag_count=len(selected_tag_ids),
            candidate_count=total,
            legacy_filter_used=legacy_filter_used,
            query_present=False,
        )
        return items, total

    rows = candidate_query.all()
    ranking_parts = [query_text, str(preferred_style or "")]
    ranking_parts.extend(spec.value for spec in facet_specs)
    ranking_parts.extend(tag.value for tag in explicit_tags.values())
    raw_query_text = " ".join(part for part in ranking_parts if part).strip()
    expanded_query_text = _expand_visual_query(raw_query_text)
    query_variants = list(
        dict.fromkeys(
            item for item in (raw_query_text, expanded_query_text) if item
        )
    )
    query_vectors = [build_hash_embedding(item) for item in query_variants]
    query_term_variants = [_terms(item) for item in query_variants]
    scored: list[dict[str, Any]] = []
    for asset, storage in rows:
        asset_terms = _terms(
            " ".join(_flatten_strings(asset.taxonomy or {}) + list(asset.tags or []))
        )
        asset_vector = _embedding_for_asset(db, asset, matcher_version)
        semantic = max(
            (_cosine(vector, asset_vector) for vector in query_vectors), default=0.0
        )
        taxonomy_score = max(
            (
                len(query_terms & asset_terms) / max(1, len(query_terms))
                for query_terms in query_term_variants
            ),
            default=1.0,
        )
        style_score = 1.0 if not preferred_style else float(
            (asset.style or "").lower() == preferred_style.lower()
        )
        quality_score = float(asset.quality_score or 0.0)
        base_score = (
            0.45 * semantic
            + 0.35 * taxonomy_score
            + 0.10 * style_score
            + 0.10 * quality_score
        )
        repeat_penalty = max(
            _recent_exact_match_penalty(asset.id, recent_asset_ids),
            _recent_exact_match_penalty(storage.id, recent_storage_ids),
        )
        score = max(0.0, base_score - repeat_penalty)
        conflicts: list[str] = []
        if preferred_style and not style_score:
            conflicts.append("style_mismatch")
        if repeat_penalty:
            conflicts.append("recent_exact_repeat")
        confidence = _confidence(score)
        scored.append(
            library_asset_dict(
                asset,
                storage,
                score=round(score, 6),
                confidence=confidence,
                score_breakdown={
                    "semantic": round(semantic, 6),
                    "taxonomy": round(taxonomy_score, 6),
                    "style": round(style_score, 6),
                    "quality": round(quality_score, 6),
                    "base_score": round(base_score, 6),
                    "repeat_penalty": round(repeat_penalty, 6),
                },
                conflicts=conflicts,
                fallback_reason="low_match_score" if confidence == "low" else None,
            )
        )
    scored.sort(
        key=lambda item: (
            item["score"],
            item["quality_score"],
            item["stable_key"],
        ),
        reverse=True,
    )
    _log_library_query(
        operation="search",
        started_at=started_at,
        catalog_version=catalog_version,
        asset_type=normalized_type,
        selected_tag_count=len(selected_tag_ids),
        candidate_count=len(scored),
        legacy_filter_used=legacy_filter_used,
        query_present=bool(query_text),
    )
    return scored[offset : offset + limit], len(scored)


def get_library_facets(
    db: Session,
    *,
    catalog_version: str,
    asset_type: str,
    selected_tag_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return set-based facet counts for one catalog asset type."""

    started_at = perf_counter()
    normalized_type = _normalize_asset_type(asset_type)
    selected_tags = _validate_library_tag_ids(
        db,
        catalog_version=catalog_version,
        asset_type=normalized_type,
        tag_ids=selected_tag_ids,
    )
    selected_ids = list(selected_tags)

    eligible_assets = (
        select(LibraryAsset.id.label("asset_id"))
        .join(StorageObject, StorageObject.id == LibraryAsset.storage_object_id)
        .where(
            *_eligible_library_conditions(
                catalog_version=catalog_version,
                asset_type=normalized_type,
            )
        )
        .cte("eligible_library_assets")
    )
    if selected_ids:
        selected_matches = (
            select(LibraryAssetTag.library_asset_id.label("asset_id"))
            .where(LibraryAssetTag.tag_id.in_(selected_ids))
            .group_by(LibraryAssetTag.library_asset_id)
            .having(
                func.count(func.distinct(LibraryAssetTag.tag_id))
                == len(selected_ids)
            )
            .cte("selected_tag_matches")
        )
        selected_assets = (
            select(eligible_assets.c.asset_id)
            .join(
                selected_matches,
                selected_matches.c.asset_id == eligible_assets.c.asset_id,
            )
            .cte("selected_library_assets")
        )
    else:
        selected_assets = eligible_assets

    total = int(
        db.execute(select(func.count()).select_from(selected_assets)).scalar_one()
    )
    option_universe = (
        select(
            LibraryTag.id.label("tag_id"),
            LibraryTag.category.label("category"),
            LibraryTag.value.label("value"),
            LibraryTag.display_name.label("display_name"),
        )
        .join(LibraryAssetTag, LibraryAssetTag.tag_id == LibraryTag.id)
        .join(
            eligible_assets,
            eligible_assets.c.asset_id == LibraryAssetTag.library_asset_id,
        )
        .distinct()
        .cte("facet_option_universe")
    )
    option_counts = (
        select(
            LibraryAssetTag.tag_id.label("tag_id"),
            func.count(func.distinct(selected_assets.c.asset_id)).label("option_count"),
        )
        .select_from(selected_assets)
        .join(
            LibraryAssetTag,
            LibraryAssetTag.library_asset_id == selected_assets.c.asset_id,
        )
        .group_by(LibraryAssetTag.tag_id)
        .cte("facet_option_counts")
    )
    rows = db.execute(
        select(
            option_universe.c.tag_id,
            option_universe.c.category,
            option_universe.c.value,
            option_universe.c.display_name,
            func.coalesce(option_counts.c.option_count, 0).label("option_count"),
        )
        .select_from(option_universe)
        .outerjoin(
            option_counts,
            option_counts.c.tag_id == option_universe.c.tag_id,
        )
        .order_by(
            option_universe.c.category.asc(),
            option_universe.c.display_name.asc(),
            option_universe.c.value.asc(),
            option_universe.c.tag_id.asc(),
        )
    ).mappings()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        tag_id = str(row["tag_id"])
        grouped.setdefault(str(row["category"]), []).append(
            {
                "id": tag_id,
                "value": str(row["value"]),
                "display_name": str(row["display_name"]),
                "count": total if tag_id in selected_tags else int(row["option_count"]),
                "selected": tag_id in selected_tags,
            }
        )
    _log_library_query(
        operation="facets",
        started_at=started_at,
        catalog_version=catalog_version,
        asset_type=normalized_type,
        selected_tag_count=len(selected_ids),
        candidate_count=total,
        legacy_filter_used=False,
        query_present=False,
    )
    return {
        "catalog_version": catalog_version,
        "asset_type": normalized_type,
        "selected_tag_ids": selected_ids,
        "total": total,
        "facets": [
            {"category": category, "options": options}
            for category, options in grouped.items()
        ],
    }


def get_library_asset(db: Session, asset_id: str, *, catalog_version: str | None = None) -> tuple[LibraryAsset, StorageObject]:
    query = (
        db.query(LibraryAsset, StorageObject)
        .join(StorageObject, StorageObject.id == LibraryAsset.storage_object_id)
        .filter(
            LibraryAsset.id == asset_id,
            LibraryAsset.enabled.is_(True),
            LibraryAsset.safety_status == "approved",
            StorageObject.status == "active",
            StorageObject.deleted_at.is_(None),
        )
    )
    if catalog_version:
        query = query.filter(LibraryAsset.catalog_version == catalog_version)
    result = query.first()
    if not result:
        raise HTTPException(status_code=404, detail="素材库条目不存在")
    return result


def _image_has_effective_alpha(image: Image.Image) -> bool:
    has_channel = "A" in image.getbands()
    has_palette_transparency = image.mode == "P" and "transparency" in image.info
    if not has_channel and not has_palette_transparency:
        return False
    alpha = image.convert("RGBA").getchannel("A")
    alpha_min, _ = alpha.getextrema()
    return alpha_min < 255


def _sanitize_image(raw: bytes) -> tuple[bytes, str, str, int, int, bool]:
    try:
        with Image.open(BytesIO(raw)) as source:
            image_format = str(source.format or "").upper()
            if image_format not in UPLOAD_FORMATS:
                raise HTTPException(status_code=415, detail="仅支持 PNG/JPEG/WebP")
            if int(getattr(source, "n_frames", 1)) != 1:
                raise HTTPException(status_code=422, detail="不支持动画或多帧图片")
            width, height = source.size
            if min(width, height) < MIN_IMAGE_SIDE or max(width, height) > MAX_IMAGE_SIDE:
                raise HTTPException(status_code=422, detail="图片宽高必须在 64 到 8192 像素之间")
            if width * height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=422, detail="图片总像素不能超过 4000 万")
            source.load()
            normalized = ImageOps.exif_transpose(source)
            has_alpha = _image_has_effective_alpha(normalized)
            if normalized.mode == "P":
                normalized = normalized.convert("RGBA" if has_alpha else "RGB")
            else:
                normalized = Image.frombytes(
                    normalized.mode,
                    normalized.size,
                    normalized.tobytes(),
                )
            mime, suffix = UPLOAD_FORMATS[image_format]
            output = BytesIO()
            if image_format == "JPEG":
                if normalized.mode not in {"RGB", "L"}:
                    background = Image.new("RGB", normalized.size, "white")
                    if "A" in normalized.getbands():
                        background.paste(normalized, mask=normalized.getchannel("A"))
                    else:
                        background.paste(normalized.convert("RGB"))
                    normalized = background
                normalized.save(output, format="JPEG", quality=95, optimize=True)
                has_alpha = False
            elif image_format == "WEBP":
                normalized.save(output, format="WEBP", quality=95, method=6)
            else:
                normalized.save(output, format="PNG", optimize=True)
            return output.getvalue(), mime, suffix, normalized.width, normalized.height, has_alpha
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="图片损坏或无法完整解码") from exc


def _create_logical_asset(
    db: Session,
    *,
    project_id: int,
    asset_type: str,
    target_name: str,
    logical_key: str,
    source_kind: str,
    source_id: str,
    taxonomy: dict[str, Any],
    status: str = "pending",
    image_url: str | None = None,
    prompt: str | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> Asset:
    existing = (
        db.query(Asset)
        .filter(
            Asset.project_id == project_id,
            Asset.asset_type == asset_type,
            Asset.logical_key == logical_key,
        )
        .first()
    )
    if existing:
        existing.archived_at = None
        return existing
    asset = Asset(
        project_id=project_id,
        asset_type=asset_type,
        target_name=target_name,
        prompt=prompt,
        image_url=image_url,
        status=status,
        logical_key=logical_key,
        taxonomy_json=taxonomy,
        genre=taxonomy.get("genre") or taxonomy.get("style"),
        description_cn=taxonomy.get("description_cn") or prompt,
        character_id=taxonomy.get("character_id") or taxonomy.get("identity_group"),
        emotion=taxonomy.get("expression") or taxonomy.get("emotion"),
        outfit=taxonomy.get("outfit"),
        pose=taxonomy.get("pose"),
        scene_location=taxonomy.get("location") or taxonomy.get("scene_location"),
        mood=taxonomy.get("mood") or taxonomy.get("atmosphere"),
        generation_params={
            "v2": {
                "logical_key": logical_key,
                "source_kind": source_kind,
                "source_revision_id": source_id,
                "taxonomy": taxonomy,
                **dict(extra_meta or {}),
            }
        },
    )
    db.add(asset)
    db.flush()
    return asset


def _create_version(
    db: Session,
    *,
    asset: Asset,
    storage: StorageObject,
    source_kind: str,
    source_id: str,
    cache_material: dict[str, Any],
    width: int | None,
    height: int | None,
    safety_status: str,
    rights_metadata: dict[str, Any],
    provider: str | None = None,
    model: str | None = None,
    prompt: str = "",
    generation_task_id: str | None = None,
) -> AssetVersion:
    cache_key = content_hash(cache_material)
    existing = (
        db.query(AssetVersion)
        .filter(AssetVersion.asset_id == asset.id, AssetVersion.cache_key == cache_key)
        .first()
    )
    if existing:
        return existing
    version_no = int(
        db.query(func.max(AssetVersion.version_no)).filter(AssetVersion.asset_id == asset.id).scalar() or 0
    ) + 1
    version = AssetVersion(
        asset_id=asset.id,
        source_kind=source_kind,
        source_revision_id=source_id,
        source_hash=content_hash(
            {
                "source_kind": source_kind,
                "source_id": source_id,
                "cache_material": cache_material,
            }
        ),
        generation_task_id=generation_task_id,
        storage_object_id=storage.id,
        version_no=version_no,
        cache_key=cache_key,
        provider=provider,
        model=model,
        prompt=prompt,
        prompt_hash=content_hash(prompt),
        prompt_version="visual-asset-v1",
        negative_prompt_hash=content_hash(""),
        width=width,
        height=height,
        safety_status=safety_status,
        rights_metadata=rights_metadata,
        asset_spec_json={
            "asset_type": asset.asset_type,
            "logical_key": asset.logical_key,
            "target_name": asset.target_name,
            "taxonomy": dict(asset.taxonomy_json or {}),
        },
        render_spec_json={
            "origin": cache_material.get("origin"),
            "prompt": prompt,
            "provider": provider,
            "model": model,
            "width": width,
            "height": height,
        },
        render_spec_hash=content_hash(
            {
                "origin": cache_material.get("origin"),
                "prompt": prompt,
                "provider": provider,
                "model": model,
                "width": width,
                "height": height,
            }
        ),
    )
    db.add(version)
    db.flush()
    return version


def upload_visual_asset(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    raw: bytes,
    filename: str,
    declared_content_type: str,
    asset_type: str,
    target_name: str,
    description: str,
    ownership_scope: str,
    reading_session_id: str | None,
    target: dict[str, Any],
    rights_attested: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    key = _validate_idempotency_key(idempotency_key)
    if not rights_attested:
        raise HTTPException(status_code=422, detail="必须确认拥有上传图片的使用权")
    if ownership_scope not in {"session", "personal", "project"}:
        raise HTTPException(status_code=422, detail="ownership_scope 无效")
    if ownership_scope == "session":
        if not reading_session_id:
            raise HTTPException(status_code=422, detail="会话素材必须提供 reading_session_id")
        reading = get_owned_session(db, reading_session_id, user_id)
        if reading.project_id != project_id:
            raise HTTPException(status_code=404, detail="阅读会话不属于该项目")
    normalized_type = _normalize_asset_type(asset_type)
    request_material = {
        "project_id": project_id,
        "filename": filename,
        "declared_content_type": declared_content_type,
        "asset_type": normalized_type,
        "target_name": target_name,
        "description": description,
        "ownership_scope": ownership_scope,
        "reading_session_id": reading_session_id,
        "target": target,
        "raw_sha256": sha256(raw).hexdigest(),
    }
    digest = _request_hash(request_material)
    existing_action = (
        db.query(AssetAction)
        .filter(AssetAction.user_id == user_id, AssetAction.idempotency_key == key)
        .first()
    )
    if existing_action:
        if existing_action.request_hash != digest:
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求")
        version_id = (existing_action.result_asset_version_ids or [None])[0]
        return _upload_result(db, existing_action, version_id, ownership_scope, True)

    sanitized, mime, suffix, width, height, has_alpha = _sanitize_image(raw)
    normalized_sha = sha256(sanitized).hexdigest()
    storage = (
        db.query(StorageObject)
        .filter(
            StorageObject.owner_id == user_id,
            StorageObject.project_id == project_id,
            StorageObject.sha256 == normalized_sha,
            StorageObject.status != "deleted",
            StorageObject.deleted_at.is_(None),
        )
        .first()
    )
    reused = storage is not None
    if storage is None:
        backend = get_local_storage_backend()
        stored = backend.save_stream(
            BytesIO(sanitized),
            namespace="private-visual-uploads",
            filename_or_suffix=suffix,
            media_type=mime,
        )
        storage = register_stored_file(
            db,
            stored=stored,
            owner_id=user_id,
            project_id=project_id,
            visibility="private",
            status="quarantined",
            backend=backend,
        )
    source_kind = "reading_continuation" if reading_session_id else "upload"
    source_id = reading_session_id or f"upload:{normalized_sha}"
    taxonomy = {
        "description_cn": description,
        "width": width,
        "height": height,
        "has_alpha": has_alpha,
    }
    asset = _create_logical_asset(
        db,
        project_id=project_id,
        asset_type=normalized_type,
        target_name=(target_name or filename or "uploaded asset")[:200],
        logical_key=f"upload:{user_id}:{normalized_sha}",
        source_kind=source_kind,
        source_id=source_id,
        taxonomy=taxonomy,
        status="quarantined",
        image_url=_media_url(storage.id),
        prompt=description,
        extra_meta={"ownership_scope": ownership_scope},
    )
    version = _create_version(
        db,
        asset=asset,
        storage=storage,
        source_kind=source_kind,
        source_id=source_id,
        cache_material={"origin": "upload", "sha256": normalized_sha, "asset_type": normalized_type},
        width=width,
        height=height,
        safety_status="pending",
        rights_metadata={
            "origin": "upload",
            "rights_attested": True,
            "ownership_scope": ownership_scope,
            "has_alpha": has_alpha,
            "privacy_metadata_removed": True,
        },
        prompt=description,
    )
    action = AssetAction(
        user_id=user_id,
        project_id=project_id,
        reading_session_id=reading_session_id,
        mode="upload",
        selection_method="manual_upload",
        origin="upload",
        status="ready",
        stage="quarantined",
        progress=100,
        target=target,
        input={
            "asset_type": normalized_type,
            "target_name": target_name,
            "ownership_scope": ownership_scope,
        },
        request_hash=digest,
        idempotency_key=key,
        result_asset_version_ids=[version.id],
        requires_confirmation=False,
    )
    db.add(action)
    db.flush()
    return _upload_result(db, action, version.id, ownership_scope, reused)


def upload_existing_asset_version(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    asset_id: int,
    raw: bytes,
    filename: str,
    declared_content_type: str,
    description: str,
    rights_attested: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    key = _validate_idempotency_key(idempotency_key)
    if not rights_attested:
        raise HTTPException(status_code=422, detail="必须确认拥有上传图片的使用权")
    asset = (
        db.query(Asset)
        .filter(
            Asset.id == asset_id,
            Asset.project_id == project_id,
            Asset.asset_type.in_(sorted(VISUAL_TYPES)),
            Asset.archived_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    request_material = {
        "project_id": project_id,
        "asset_id": asset.id,
        "filename": filename,
        "declared_content_type": declared_content_type,
        "description": description,
        "raw_sha256": sha256(raw).hexdigest(),
    }
    digest = _request_hash(request_material)
    existing_action = (
        db.query(AssetAction)
        .filter(AssetAction.user_id == user_id, AssetAction.idempotency_key == key)
        .first()
    )
    if existing_action:
        if existing_action.request_hash != digest:
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求")
        version_id = (existing_action.result_asset_version_ids or [None])[0]
        return _upload_result(db, existing_action, version_id, "project", True)

    sanitized, mime, suffix, width, height, has_alpha = _sanitize_image(raw)
    normalized_sha = sha256(sanitized).hexdigest()
    storage = (
        db.query(StorageObject)
        .filter(
            StorageObject.owner_id == user_id,
            StorageObject.project_id == project_id,
            StorageObject.sha256 == normalized_sha,
            StorageObject.status != "deleted",
            StorageObject.deleted_at.is_(None),
        )
        .first()
    )
    reused = storage is not None
    if storage is None:
        backend = get_local_storage_backend()
        stored = backend.save_stream(
            BytesIO(sanitized),
            namespace="private-visual-asset-versions",
            filename_or_suffix=suffix,
            media_type=mime,
        )
        storage = register_stored_file(
            db,
            stored=stored,
            owner_id=user_id,
            project_id=project_id,
            visibility="private",
            status="active",
            backend=backend,
        )
    else:
        storage.status = "active"
        storage.visibility = "private"
    version = _create_version(
        db,
        asset=asset,
        storage=storage,
        source_kind="upload",
        source_id=f"upload:{normalized_sha[:29]}",
        cache_material={
            "origin": "asset_version_upload",
            "asset_id": asset.id,
            "sha256": normalized_sha,
        },
        width=width,
        height=height,
        safety_status="approved",
        rights_metadata={
            "origin": "upload",
            "rights_attested": True,
            "ownership_scope": "project",
            "has_alpha": has_alpha,
            "privacy_metadata_removed": True,
        },
        provider="manual-upload",
        model=None,
        prompt=description,
    )
    asset.status = "completed"
    asset.image_url = _media_url(storage.id)
    action = AssetAction(
        user_id=user_id,
        project_id=project_id,
        mode="upload",
        selection_method="manual_upload",
        origin="upload",
        status="ready",
        stage="active",
        progress=100,
        target={
            "source_kind": "upload",
            "source_id": f"asset:{asset.id}",
            "asset_id": asset.id,
        },
        input={
            "asset_id": asset.id,
            "asset_type": asset.asset_type,
            "target_name": asset.target_name,
        },
        request_hash=digest,
        idempotency_key=key,
        result_asset_version_ids=[version.id],
        requires_confirmation=False,
    )
    db.add(action)
    db.flush()
    return _upload_result(db, action, version.id, "project", reused)


def _upload_result(
    db: Session,
    action: AssetAction,
    version_id: str | None,
    ownership_scope: str,
    reused: bool,
) -> dict[str, Any]:
    version = db.query(AssetVersion).filter(AssetVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=409, detail="上传记录缺少素材版本")
    asset = db.query(Asset).filter(Asset.id == version.asset_id).one()
    storage = db.query(StorageObject).filter(StorageObject.id == version.storage_object_id).one()
    rights = dict(version.rights_metadata or {})
    return {
        "action_id": action.id,
        "asset_id": asset.id,
        "asset_version_id": version.id,
        "storage_object_id": storage.id,
        "asset_type": asset.asset_type,
        "target_name": asset.target_name or "",
        "ownership_scope": ownership_scope,
        "media_url": _media_url(storage.id),
        "media_type": storage.media_type,
        "sha256": storage.sha256,
        "width": int(version.width or 0),
        "height": int(version.height or 0),
        "has_alpha": bool(rights.get("has_alpha")),
        "status": storage.status,
        "reused_storage_object": reused,
    }


def _find_materialized_library_asset(
    db: Session, project_id: int, library_asset_id: str
) -> Asset | None:
    library_asset = db.query(LibraryAsset).filter(LibraryAsset.id == library_asset_id).first()
    if not library_asset:
        return None
    return (
        db.query(Asset)
        .filter(
            Asset.project_id == project_id,
            Asset.asset_type == library_asset.asset_type,
            Asset.logical_key
            == f"library:{library_asset.catalog_version}:{library_asset.stable_key}",
        )
        .first()
    )


def materialize_library_asset(
    db: Session,
    *,
    project_id: int,
    library_asset: LibraryAsset,
    storage: StorageObject,
    source_kind: str,
    source_id: str,
    target_asset: Asset | None = None,
) -> AssetVersion:
    asset = target_asset or _find_materialized_library_asset(db, project_id, library_asset.id)
    if asset is None:
        taxonomy = dict(library_asset.taxonomy or {})
        taxonomy.update(
            {
                "description_cn": library_asset.description_cn,
                "identity_group": library_asset.identity_group,
                "expression": library_asset.expression,
                "pose": library_asset.pose,
                "style": library_asset.style,
            }
        )
        asset = _create_logical_asset(
            db,
            project_id=project_id,
            asset_type=library_asset.asset_type,
            target_name=(library_asset.description_cn or library_asset.stable_key)[:200],
            logical_key=f"library:{library_asset.catalog_version}:{library_asset.stable_key}",
            source_kind=source_kind,
            source_id=source_id,
            taxonomy=taxonomy,
            status="completed",
            image_url=_media_url(storage.id),
            prompt=library_asset.description_cn,
            extra_meta={"library_asset_id": library_asset.id},
        )
    version = _create_version(
        db,
        asset=asset,
        storage=storage,
        source_kind=source_kind,
        source_id=source_id,
        cache_material={
            "origin": "library",
            "catalog_version": library_asset.catalog_version,
            "stable_key": library_asset.stable_key,
            "storage_sha256": storage.sha256,
        },
        width=(library_asset.taxonomy or {}).get("width"),
        height=(library_asset.taxonomy or {}).get("height"),
        safety_status="passed",
        rights_metadata={
            **dict(library_asset.rights_metadata or {}),
            "origin": "library",
            "catalog_version": library_asset.catalog_version,
            "library_asset_id": library_asset.id,
        },
        provider="catalog",
        model=library_asset.catalog_version,
        prompt=library_asset.description_cn,
    )
    asset.status = "completed"
    return version


def _asset_action_selection(mode: str, system_generate: bool = False) -> str:
    if system_generate:
        return "system_generate"
    return {
        "agent_compose": "agent",
        "direct_generate": "direct_generate",
        "library_select": "manual_library",
        "upload": "manual_upload",
    }[mode]


def _project_source_prompt(db: Session, project_id: int) -> str:
    project = db.query(Project).filter(Project.id == project_id).first()
    source_work = str(getattr(project, "source_work", "") or "").strip()
    if not source_work:
        return ""
    return (
        f"二创原作：{source_work}\n"
        "生成视觉素材时必须保留原作可识别的角色设计、世界观视觉符号和作品气质。"
    )


def _compose_direct_generation_prompt(
    db: Session,
    *,
    project_id: int,
    instruction: str,
) -> tuple[str, str]:
    user_prompt = instruction.strip()
    if not user_prompt:
        raise HTTPException(status_code=422, detail="direct_generate 需要 input.instruction")
    source_prompt = _project_source_prompt(db, project_id)
    if not source_prompt:
        return user_prompt, ""
    return f"{source_prompt}\n\n用户图片生成要求：{user_prompt}", source_prompt


def _graph_node_key_values(node: dict[str, Any]) -> set[str]:
    result = {
        str(value)
        for value in (
            node.get("node_key"),
            node.get("scene_id"),
            node.get("paragraph_id"),
        )
        if value is not None and str(value)
    }
    data = node.get("Data")
    if isinstance(data, dict):
        for field in ("SceneId", "ParagraphId"):
            value = data.get(field)
            if isinstance(value, dict) and value.get("Kind") == "String":
                string_value = str(value.get("StringValue") or "").strip()
                if string_value:
                    result.add(string_value)
    return result


def _find_graph_node(revision: VNGraphRevision, target: dict[str, Any]) -> dict[str, Any]:
    graph = revision.graph_json if isinstance(revision.graph_json, dict) else {}
    nodes = graph.get("Nodes")
    if not isinstance(nodes, list):
        raise HTTPException(status_code=409, detail="VNGraph revision 缺少 Nodes")
    node_index = target.get("node_index")
    node_key = str(target.get("node_key") or "").strip()
    if node_index is not None:
        matches = [
            node for node in nodes
            if isinstance(node, dict) and node.get("Index") == node_index
        ]
    elif node_key:
        matches = [
            node for node in nodes
            if isinstance(node, dict) and node_key in _graph_node_key_values(node)
        ]
    else:
        raise HTTPException(status_code=422, detail="VNGraph target 需要 node_index 或 node_key")
    if len(matches) != 1:
        raise HTTPException(status_code=422, detail="VNGraph 节点定位不唯一")
    return matches[0]


def _normalize_action_target(db: Session, project_id: int, target: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(target)
    resource_slot_id = str(normalized.get("resource_slot_id") or "").strip()
    if resource_slot_id:
        slot = (
            db.query(ScriptResourceSlot)
            .filter(
                ScriptResourceSlot.id == resource_slot_id,
                ScriptResourceSlot.project_id == project_id,
            )
            .first()
        )
        if not slot:
            raise HTTPException(status_code=404, detail="Script resource slot 不存在")
        role = slot.role
        normalized.update(
            {
                "resource_slot_id": slot.id,
                "asset_id": slot.asset_id,
                "source_kind": "chapter_script_revision",
                "source_id": slot.chapter_script_revision_id,
                "node_key": slot.scene_id,
                "segment_key": slot.paragraph_id,
                "role": role,
                "asset_slot": ROLE_TO_ASSET_SLOT.get(role),
            }
        )
        return normalized

    graph_revision_id = str(
        normalized.get("vn_graph_revision_id")
        or normalized.get("graph_revision_id")
        or (
            normalized.get("source_id")
            if normalized.get("source_kind") == "vn_graph_revision"
            else ""
        )
        or ""
    ).strip()
    if graph_revision_id:
        revision = (
            db.query(VNGraphRevision)
            .filter(
                VNGraphRevision.id == graph_revision_id,
                VNGraphRevision.project_id == project_id,
            )
            .first()
        )
        if not revision:
            raise HTTPException(status_code=404, detail="VNGraph revision 不存在")
        node = _find_graph_node(revision, normalized)
        inferred = VNGRAPH_NODE_ASSET_SLOT.get((node.get("NodeType"), node.get("SubType")))
        if not inferred:
            raise HTTPException(status_code=422, detail="VNGraph 节点不是可替换图片节点")
        asset_slot, role = inferred
        normalized.update(
            {
                "vn_graph_revision_id": revision.id,
                "source_kind": "vn_graph_revision",
                "source_id": revision.id,
                "graph_revision_id": revision.id,
                "graph_hash": normalized.get("graph_hash") or revision.graph_hash,
                "asset_slot": asset_slot,
                "role": role,
            }
        )
        return normalized

    if normalized.get("source_kind") == "vn_graph_revision" and not normalized.get("graph_revision_id"):
        normalized["graph_revision_id"] = normalized.get("source_id")
    if normalized.get("asset_slot") and not normalized.get("role"):
        normalized["role"] = ASSET_SLOT_TO_ROLE.get(normalized["asset_slot"])
    if normalized.get("role") and not normalized.get("asset_slot"):
        normalized["asset_slot"] = ROLE_TO_ASSET_SLOT.get(normalized["role"])
    return normalized


def _validate_action_target(db: Session, project_id: int, target: dict[str, Any]) -> None:
    if target.get("source_kind") not in SOURCE_KINDS:
        raise HTTPException(status_code=422, detail="source_kind 无效")
    if target.get("role") not in ROLE_TO_ASSET_SLOT:
        raise HTTPException(status_code=422, detail="target 无法推断素材类型")
    if target.get("asset_slot") not in ASSET_SLOT_TO_ROLE:
        raise HTTPException(status_code=422, detail="target 无法推断素材槽位")
    graph_revision_id = target.get("graph_revision_id")
    graph_hash = target.get("graph_hash")
    if graph_revision_id:
        revision = (
            db.query(VNGraphRevision)
            .filter(
                VNGraphRevision.id == graph_revision_id,
                VNGraphRevision.project_id == project_id,
            )
            .first()
        )
        if not revision:
            raise HTTPException(status_code=404, detail="VNGraph revision 不存在")
        if graph_hash and revision.graph_hash != graph_hash:
            raise HTTPException(status_code=409, detail={"code": "graph.version_conflict", "current": revision.graph_hash})


def _select_project_asset_version(
    db: Session,
    *,
    project_id: int,
    target: dict[str, Any],
    input_data: dict[str, Any],
) -> AssetVersion:
    version_id = str(input_data.get("asset_version_id") or "").strip()
    asset_id_value = input_data.get("asset_id")
    expected_type = target.get("role")
    query = (
        db.query(AssetVersion)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .join(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(
            Asset.project_id == project_id,
            Asset.asset_type.in_(sorted(VISUAL_TYPES)),
            Asset.archived_at.is_(None),
            StorageObject.status != "deleted",
            StorageObject.deleted_at.is_(None),
        )
    )
    if version_id:
        query = query.filter(AssetVersion.id == version_id)
    else:
        if asset_id_value is None or str(asset_id_value).strip() == "":
            raise HTTPException(status_code=422, detail="upload 需要 input.asset_id 或 input.asset_version_id")
        try:
            asset_id = int(asset_id_value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="input.asset_id 必须是整数") from exc
        query = query.filter(Asset.id == asset_id).order_by(
            AssetVersion.version_no.desc(),
            AssetVersion.created_at.desc(),
        )
    version = query.first()
    if not version:
        raise HTTPException(status_code=404, detail="项目素材版本不存在")
    asset = db.query(Asset).filter(Asset.id == version.asset_id).one()
    if expected_type and asset.asset_type != expected_type:
        raise HTTPException(status_code=409, detail="项目素材类型和目标槽位不匹配")
    return version


def _build_patch(target: dict[str, Any], versions: list[AssetVersion]) -> list[dict[str, Any]]:
    patch: list[dict[str, Any]] = []
    for index, version in enumerate(versions):
        asset = version and getattr(version, "_visual_asset", None)
        slot = target.get("asset_slot") or ROLE_TO_ASSET_SLOT.get(str(target.get("role") or ""))
        if len(versions) > 1 and asset is not None:
            slot = ROLE_TO_ASSET_SLOT.get(asset.asset_type, slot)
        if slot not in ASSET_SLOT_TO_ROLE:
            raise HTTPException(status_code=422, detail="VNGraph patch 无法推断素材槽位")
        if not version.storage_object_id:
            raise HTTPException(
                status_code=409,
                detail="VNGraph patch 素材缺少不可变存储对象",
            )
        item = {
            "op": "set_node_data",
            "path": [slot],
            "value": {
                "Kind": "String",
                "StringValue": _media_url(version.storage_object_id),
            },
        }
        if target.get("node_index") is not None:
            item["node_index"] = target["node_index"]
        if target.get("node_key"):
            item["node_key"] = target["node_key"]
        if index:
            item["order_index"] = index
        patch.append(item)
    return patch


def _attach_asset(db: Session, version: AssetVersion) -> AssetVersion:
    version._visual_asset = db.query(Asset).filter(Asset.id == version.asset_id).one()
    return version


def _create_scene_manifest(
    db: Session,
    *,
    project_id: int,
    reading_session_id: str | None,
    versions: list[AssetVersion],
    target: dict[str, Any],
    matches: list[dict[str, Any]],
    catalog_version: str | None,
    matcher_version: str,
) -> SceneManifest:
    bindings = []
    for version in versions:
        asset = getattr(version, "_visual_asset", None) or db.query(Asset).filter(Asset.id == version.asset_id).one()
        bindings.append(
            {
                "asset_id": asset.id,
                "asset_version_id": version.id,
                "storage_object_id": version.storage_object_id,
                "asset_type": asset.asset_type,
                "role": asset.asset_type,
                "asset_slot": {
                    "background": "BackgroundImage",
                    "portrait": "TachiIamge",
                    "keyframe": "IllustrationImage",
                }.get(asset.asset_type, target.get("asset_slot")),
                "node_key": target.get("node_key"),
                "node_index": target.get("node_index"),
            }
        )
    payload = {
        "bindings": bindings,
        "layout": dict(target.get("layout") or {}),
        "matches": matches,
        "catalog_version": catalog_version,
        "matcher_version": matcher_version,
    }
    digest = content_hash(payload)
    existing = (
        db.query(SceneManifest)
        .filter(SceneManifest.project_id == project_id, SceneManifest.content_hash == digest)
        .first()
    )
    if existing:
        return existing
    scene = SceneManifest(
        project_id=project_id,
        reading_session_id=reading_session_id,
        bindings=bindings,
        layout=payload["layout"],
        match_summary={"items": matches},
        catalog_version=catalog_version,
        matcher_version=matcher_version,
        content_hash=digest,
    )
    db.add(scene)
    db.flush()
    return scene


def _finalize_action_with_versions(
    db: Session,
    *,
    action: AssetAction,
    versions: list[AssetVersion],
    matches: list[dict[str, Any]],
    catalog_version: str | None,
    matcher_version: str,
) -> None:
    versions = [_attach_asset(db, version) for version in versions]
    scene = _create_scene_manifest(
        db,
        project_id=action.project_id,
        reading_session_id=action.reading_session_id,
        versions=versions,
        target=dict(action.target or {}),
        matches=matches,
        catalog_version=catalog_version,
        matcher_version=matcher_version,
    )
    action.result_asset_version_ids = [version.id for version in versions]
    action.scene_manifest_id = scene.id
    target = dict(action.target or {})
    action.vngraph_patch = (
        _build_patch(target, versions)
        if target.get("source_kind") == "vn_graph_revision"
        else []
    )
    action.status = "awaiting_confirmation" if action.requires_confirmation else "ready"
    action.stage = "preview_ready"
    action.progress = 100


def _complete_asset_action_task(
    db: Session,
    *,
    action: AssetAction,
    event_type: str,
    event_payload: dict[str, Any],
    result_refs: dict[str, Any],
) -> None:
    task, _created = create_generation_task(
        db,
        user_id=action.user_id,
        project_id=action.project_id,
        kind="asset.action",
        idempotency_key=f"asset-action:{action.id}",
        source_refs={
            "action_id": action.id,
            "source_kind": (action.target or {}).get("source_kind"),
            "source_id": (action.target or {}).get("source_id"),
        },
        parameters={
            "mode": action.mode,
            "selection_method": action.selection_method,
            "target": dict(action.target or {}),
        },
        estimated_cost=0,
        enqueue=False,
    )
    action.generation_task_id = task.id
    if task.status in {"partial", "succeeded", "failed", "cancelled"}:
        return
    task.started_at = task.started_at or utcnow()
    task.status = "running"
    task.stage = action.stage
    task.progress = float(action.progress or 0)
    append_task_event(
        db,
        task,
        "asset.action.accepted",
        {"action_id": action.id, "mode": action.mode, "selection_method": action.selection_method},
    )
    append_task_event(db, task, event_type, event_payload)
    complete_task(
        db,
        task,
        result_refs={"action_id": action.id, **result_refs},
        actual_cost=0,
    )


def _queue_action_generation(
    db: Session,
    *,
    action: AssetAction,
    user_id: int,
    target: dict[str, Any],
    input_data: dict[str, Any],
    prompt: str,
    matcher_version: str,
    keep_existing_preview: bool,
) -> None:
    asset_type = _normalize_asset_type(str(input_data.get("asset_type") or target.get("role") or ""))
    portrait_prompt_contract_version = ""
    if asset_type == "portrait":
        # The queued text is a structured draft. The worker routes portraits
        # through generate_portrait(), whose LLM produces the authoritative
        # provider prompt; no deterministic portrait clauses are appended here.
        from app.services.image_generation_service import (
            PORTRAIT_GENERATION_CONTRACT_VERSION,
        )
        from app.services.prompt_rewriter_service import (
            PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION,
        )
        portrait_prompt_contract_version = PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION
    taxonomy = dict(
        input_data.get("taxonomy") or input_data.get("required_facets") or {}
    )
    logical_key = f"generated:{action.id}:{asset_type}"
    asset = _create_logical_asset(
        db,
        project_id=action.project_id,
        asset_type=asset_type,
        target_name=str(input_data.get("target_name") or prompt)[:200],
        logical_key=logical_key,
        source_kind=target["source_kind"],
        source_id=target["source_id"],
        taxonomy=taxonomy,
        status="generating",
        prompt=prompt,
        extra_meta={"asset_action_id": action.id},
    )
    render_spec = {
        "prompt": prompt,
        "provider": str(input_data.get("provider") or "configured"),
        "model": str(input_data.get("model") or "configured"),
        "prompt_template_version": str(
            input_data.get("prompt_template_version")
            or (
                portrait_prompt_contract_version
                if asset_type == "portrait"
                else "visual-action-v1"
            )
        ),
        "negative_prompt": str(input_data.get("negative_prompt") or ""),
        "negative_prompt_version": str(input_data.get("negative_prompt_version") or "none-v1"),
        "seed": input_data.get("seed"),
        "width": 768 if asset_type == "portrait" else int(input_data.get("width") or 1024),
        "height": 1280 if asset_type == "portrait" else int(input_data.get("height") or 1024),
        "style_pack_version": str(input_data.get("style_pack_version") or "default-v1"),
        "identity_version": input_data.get("identity_version"),
        "postprocess_version": (
            PORTRAIT_GENERATION_CONTRACT_VERSION
            if asset_type == "portrait"
            else str(input_data.get("postprocess_version") or "none-v1")
        ),
        "validator_version": (
            "portrait-alpha-v1"
            if asset_type == "portrait"
            else str(input_data.get("validator_version") or "visual-qc-v1")
        ),
        "extra_parameters": dict(input_data.get("extra_parameters") or {}),
    }
    variant = request_asset_variant(
        db,
        user_id=user_id,
        project_id=action.project_id,
        asset_id=asset.id,
        source_kind=target["source_kind"],
        source_revision_id=target["source_id"],
        render_spec=render_spec,
    )
    if variant["version"]:
        action.origin = "ai_generated"
        _finalize_action_with_versions(
            db,
            action=action,
            versions=[variant["version"]],
            matches=[],
            catalog_version=None,
            matcher_version=matcher_version,
        )
        _complete_asset_action_task(
            db,
            action=action,
            event_type="asset.render.cache_hit",
            event_payload={"asset_version_id": variant["version"].id},
            result_refs={
                "status": action.status,
                "asset_version_ids": list(action.result_asset_version_ids or []),
                "cache_hit": True,
            },
        )
        return
    action.generation_task_id = variant["task"].id
    append_task_event(
        db,
        variant["task"],
        "asset.action.accepted",
        {"action_id": action.id, "mode": action.mode, "selection_method": action.selection_method},
    )
    append_task_event(
        db,
        variant["task"],
        "asset.render.requested",
        {"action_id": action.id, "asset_id": asset.id, "asset_type": asset.asset_type},
    )
    if keep_existing_preview:
        action.status = "awaiting_confirmation"
        action.stage = "fallback_ready_generating_upgrade"
        action.progress = 100
    else:
        action.status = "processing"
        action.stage = "generating"
        action.progress = max(5, float(variant["task"].progress or 0))


def create_asset_action(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    mode: str,
    target: dict[str, Any],
    input_data: dict[str, Any],
    idempotency_key: str,
    catalog_version: str,
    matcher_version: str,
    agent_enabled: bool,
    system_generate: bool = False,
    reading_session_id: str | None = None,
) -> AssetAction:
    key = _validate_idempotency_key(idempotency_key)
    if mode not in {"agent_compose", "direct_generate", "library_select", "upload"}:
        raise HTTPException(status_code=422, detail="mode 无效")
    if mode == "agent_compose" and not agent_enabled:
        raise HTTPException(status_code=503, detail="Agent 编排功能尚未启用")
    target = _normalize_action_target(db, project_id, target)
    _validate_action_target(db, project_id, target)
    digest = _request_hash({"mode": mode, "target": target, "input": input_data})
    existing = (
        db.query(AssetAction)
        .filter(AssetAction.user_id == user_id, AssetAction.idempotency_key == key)
        .first()
    )
    if existing:
        if existing.request_hash != digest:
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求")
        return refresh_asset_action(db, existing)

    action = AssetAction(
        user_id=user_id,
        project_id=project_id,
        reading_session_id=reading_session_id,
        mode=mode,
        selection_method=_asset_action_selection(mode, system_generate),
        status="processing",
        stage="accepted",
        progress=5,
        target=target,
        input=input_data,
        request_hash=digest,
        idempotency_key=key,
        base_graph_hash=target.get("graph_hash"),
        requires_confirmation=True,
    )
    db.add(action)
    db.flush()

    if mode == "upload":
        version = _select_project_asset_version(
            db,
            project_id=project_id,
            target=target,
            input_data=input_data,
        )
        asset = db.query(Asset).filter(Asset.id == version.asset_id).one()
        storage = db.query(StorageObject).filter(StorageObject.id == version.storage_object_id).one()
        action.origin = "upload"
        _finalize_action_with_versions(
            db,
            action=action,
            versions=[version],
            matches=[
                {
                    "id": str(asset.id),
                    "asset_id": asset.id,
                    "asset_version_id": version.id,
                    "asset_type": asset.asset_type,
                    "storage_object_id": storage.id,
                    "media_url": _media_url(storage.id),
                    "sha256": storage.sha256,
                    "description_cn": asset.description_cn or "",
                    "taxonomy": dict(asset.taxonomy_json or {}),
                    "origin": "upload",
                    "confidence": "high",
                    "score": 1.0,
                    "score_breakdown": {"manual": 1.0},
                }
            ],
            catalog_version=None,
            matcher_version=matcher_version,
        )
        _complete_asset_action_task(
            db,
            action=action,
            event_type="asset.upload.selected",
            event_payload={
                "asset_id": asset.id,
                "asset_version_ids": list(action.result_asset_version_ids or []),
            },
            result_refs={
                "status": action.status,
                "asset_version_ids": list(action.result_asset_version_ids or []),
                "scene_manifest_id": action.scene_manifest_id,
                "requires_confirmation": bool(action.requires_confirmation),
            },
        )
        return action

    if mode in {"library_select", "agent_compose"}:
        matches: list[dict[str, Any]]
        query = ""
        if mode == "library_select":
            library_id = str(input_data.get("library_asset_id") or "")
            if library_id:
                library_asset, storage = get_library_asset(
                    db, library_id, catalog_version=catalog_version
                )
                matches = [
                    library_asset_dict(
                        library_asset,
                        storage,
                        score=1.0,
                        confidence="high",
                        score_breakdown={"manual": 1.0},
                        conflicts=[],
                        fallback_reason=None,
                    )
                ]
            else:
                query = str(
                    input_data.get("instruction")
                    or input_data.get("query")
                    or input_data.get("description")
                    or ""
                ).strip()
                if not query:
                    raise HTTPException(
                        status_code=422,
                        detail="library_select 需要 input.library_asset_id 或 input.instruction",
                    )
                required_facets_input = input_data.get("required_facets")
                if required_facets_input is None:
                    required_facets = {
                        key: value
                        for key, value in dict(input_data.get("taxonomy") or {}).items()
                        if key in FACET_CATEGORIES
                    }
                else:
                    required_facets = dict(required_facets_input)
                matches, _ = search_library_assets(
                    db,
                    catalog_version=catalog_version,
                    matcher_version=matcher_version,
                    query=query,
                    asset_type=input_data.get("asset_type") or target.get("role"),
                    identity_group=input_data.get("identity_group"),
                    limit=5,
                    offset=0,
                    required_tag_ids=input_data.get("required_tag_ids") or [],
                    required_facets=required_facets,
                    preferred_style=input_data.get("preferred_style") or input_data.get("style"),
                    recent_library_asset_ids=input_data.get("recent_library_asset_ids") or [],
                    recent_storage_object_ids=input_data.get("recent_storage_object_ids") or [],
                )
                if not matches:
                    raise HTTPException(status_code=409, detail="素材库中没有满足硬约束的素材")
                matches = matches[: int(input_data.get("max_assets") or 1)]
        else:
            query = str(input_data.get("query") or input_data.get("description") or "").strip()
            if not query:
                raise HTTPException(status_code=422, detail="Agent 编排需要 query 或 description")
            required_facets_input = input_data.get("required_facets")
            if required_facets_input is None:
                required_facets = {
                    key: value
                    for key, value in dict(input_data.get("taxonomy") or {}).items()
                    if key in FACET_CATEGORIES
                }
            else:
                required_facets = dict(required_facets_input)
            matches, _ = search_library_assets(
                db,
                catalog_version=catalog_version,
                matcher_version=matcher_version,
                query=query,
                asset_type=input_data.get("asset_type") or target.get("role"),
                identity_group=input_data.get("identity_group"),
                limit=5,
                offset=0,
                required_tag_ids=input_data.get("required_tag_ids") or [],
                required_facets=required_facets,
                preferred_style=input_data.get("preferred_style") or input_data.get("style"),
                recent_library_asset_ids=input_data.get("recent_library_asset_ids") or [],
                recent_storage_object_ids=input_data.get("recent_storage_object_ids") or [],
            )
            if not matches:
                raise HTTPException(status_code=409, detail="素材库中没有满足硬约束的素材")
            matches = matches[: int(input_data.get("max_assets") or 1)]
        versions: list[AssetVersion] = []
        for match in matches:
            library_asset, storage = get_library_asset(db, match["id"])
            target_asset = None
            if target.get("asset_id") is not None:
                target_asset = (
                    db.query(Asset)
                    .filter(
                        Asset.id == target["asset_id"],
                        Asset.project_id == project_id,
                        Asset.asset_type == library_asset.asset_type,
                    )
                    .first()
                )
                if not target_asset:
                    raise HTTPException(status_code=409, detail="目标槽位素材不存在或类型不匹配")
            versions.append(
                materialize_library_asset(
                    db,
                    project_id=project_id,
                    library_asset=library_asset,
                    storage=storage,
                    source_kind=target["source_kind"],
                    source_id=target["source_id"],
                    target_asset=target_asset,
                )
            )
        action.origin = "library"
        _finalize_action_with_versions(
            db,
            action=action,
            versions=versions,
            matches=matches,
            catalog_version=catalog_version,
            matcher_version=matcher_version,
        )
        if (
            mode == "agent_compose"
            and matches
            and matches[0].get("confidence") == "low"
            and bool(input_data.get("generate_on_low_confidence", True))
        ):
            _queue_action_generation(
                db,
                action=action,
                user_id=user_id,
                target=target,
                input_data={**input_data, "asset_type": target.get("role")},
                prompt=str(input_data.get("generation_prompt") or query),
                matcher_version=matcher_version,
                keep_existing_preview=True,
            )
            append_task_event(
                db,
                db.query(GenerationTask).filter(GenerationTask.id == action.generation_task_id).one(),
                "asset.preview.fallback_ready",
                {
                    "action_id": action.id,
                    "asset_version_ids": list(action.result_asset_version_ids or []),
                },
            )
        else:
            _complete_asset_action_task(
                db,
                action=action,
                event_type="asset.library.matched",
                event_payload={
                    "library_asset_ids": [match["id"] for match in matches],
                    "asset_version_ids": list(action.result_asset_version_ids or []),
                    "confidence": matches[0].get("confidence") if matches else None,
                },
                result_refs={
                    "status": action.status,
                    "asset_version_ids": list(action.result_asset_version_ids or []),
                    "scene_manifest_id": action.scene_manifest_id,
                    "requires_confirmation": bool(action.requires_confirmation),
                },
            )
        return action

    raw_instruction = str(
        input_data.get("instruction")
        or input_data.get("prompt")
        or input_data.get("description")
        or ""
    )
    prompt, source_prompt = _compose_direct_generation_prompt(
        db,
        project_id=project_id,
        instruction=raw_instruction,
    )
    input_data = {
        **input_data,
        "instruction": raw_instruction.strip(),
        "prompt": prompt,
        "source_prompt": source_prompt,
        "target_name": input_data.get("target_name") or raw_instruction.strip(),
    }
    action.input = input_data
    action.origin = "ai_generated"
    _queue_action_generation(
        db,
        action=action,
        user_id=user_id,
        target=target,
        input_data=input_data,
        prompt=prompt,
        matcher_version=matcher_version,
        keep_existing_preview=False,
    )
    return action


def get_owned_asset_action(db: Session, action_id: str, user_id: int, project_id: int) -> AssetAction:
    action = (
        db.query(AssetAction)
        .filter(
            AssetAction.id == action_id,
            AssetAction.user_id == user_id,
            AssetAction.project_id == project_id,
        )
        .first()
    )
    if not action:
        raise HTTPException(status_code=404, detail="素材操作不存在")
    return action


def refresh_asset_action(db: Session, action: AssetAction) -> AssetAction:
    if action.status in {"applied", "cancelled", "failed"} or not action.generation_task_id:
        return action
    task = db.query(GenerationTask).filter(GenerationTask.id == action.generation_task_id).first()
    if not task:
        if action.result_asset_version_ids:
            action.stage = "fallback_ready"
            action.error = {"code": "upgrade_task_missing", "message": "升级任务不存在，继续使用兜底素材"}
        else:
            action.status = "failed"
            action.stage = "failed"
            action.error = {"code": "task_missing", "message": "生成任务不存在"}
        return action
    if task.kind == "asset.action":
        return action
    if not action.result_asset_version_ids:
        action.progress = float(task.progress or 0)
        action.stage = task.stage or task.status
    if task.status == "succeeded":
        version_id = (task.result_refs or {}).get("asset_version_id")
        version = db.query(AssetVersion).filter(AssetVersion.id == version_id).first()
        if not version:
            action.status = "failed"
            action.error = {"code": "result_missing", "message": "任务缺少素材版本"}
        else:
            action.origin = "ai_generated"
            companion_ids = list((action.input or {}).get("companion_asset_version_ids") or [])
            companions = (
                db.query(AssetVersion).filter(AssetVersion.id.in_(companion_ids)).all()
                if companion_ids
                else []
            )
            _finalize_action_with_versions(
                db,
                action=action,
                versions=[version, *companions],
                matches=[],
                catalog_version=None,
                matcher_version=MATCHER_VERSION,
            )
    elif task.status in {"failed", "cancelled"}:
        if action.result_asset_version_ids:
            action.status = "awaiting_confirmation"
            action.stage = "fallback_ready"
            action.error = {
                "code": task.error_code or f"upgrade_{task.status}",
                "message": "后台生图未完成，继续使用已展示的兜底素材",
            }
        else:
            action.status = "cancelled" if task.status == "cancelled" else "failed"
            action.stage = task.status
            action.error = {
                "code": task.error_code or f"task_{task.status}",
                "message": task.error_detail or "素材生成未完成",
            }
    return action


def scene_manifest_dict(scene: SceneManifest | None) -> dict[str, Any] | None:
    if not scene:
        return None
    return {
        "id": scene.id,
        "bindings": list(scene.bindings or []),
        "layout": dict(scene.layout or {}),
        "match_summary": dict(scene.match_summary or {}),
        "catalog_version": scene.catalog_version,
        "matcher_version": scene.matcher_version,
        "content_hash": scene.content_hash,
    }


def asset_action_dict(db: Session, action: AssetAction) -> dict[str, Any]:
    refresh_asset_action(db, action)
    assets = []
    for version_id in action.result_asset_version_ids or []:
        row = (
            db.query(AssetVersion, Asset, StorageObject)
            .join(Asset, Asset.id == AssetVersion.asset_id)
            .join(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
            .filter(AssetVersion.id == version_id, Asset.project_id == action.project_id)
            .first()
        )
        if not row:
            continue
        version, asset, storage = row
        params = asset.generation_params if isinstance(asset.generation_params, dict) else {}
        taxonomy = dict((params.get("v2") or {}).get("taxonomy") or {})
        assets.append(
            {
                "asset_id": asset.id,
                "asset_version_id": version.id,
                "storage_object_id": storage.id,
                "asset_type": asset.asset_type,
                "media_url": _media_url(storage.id),
                "sha256": storage.sha256,
                "description_cn": asset.description_cn or "",
                "tags": list(taxonomy.get("tags") or []),
                "taxonomy": taxonomy,
                "width": version.width,
                "height": version.height,
            }
        )
    scene = (
        db.query(SceneManifest).filter(SceneManifest.id == action.scene_manifest_id).first()
        if action.scene_manifest_id
        else None
    )
    return {
        "action_id": action.id,
        "status": action.status,
        "stage": action.stage,
        "progress": float(action.progress or 0),
        "selection_method": action.selection_method,
        "origin": action.origin,
        "task_id": action.generation_task_id,
        "events_url": f"/api/tasks/{action.generation_task_id}/events" if action.generation_task_id else None,
        "assets": assets,
        "scene_manifest": scene_manifest_dict(scene),
        "vngraph_patch": list(action.vngraph_patch or []),
        "base_graph_hash": action.base_graph_hash,
        "result_graph_hash": action.result_graph_hash,
        "requires_confirmation": bool(action.requires_confirmation),
        "error": dict(action.error) if isinstance(action.error, dict) else None,
        "created_at": action.created_at,
        "updated_at": action.updated_at,
    }


def cancel_asset_action(db: Session, action: AssetAction) -> AssetAction:
    if action.status in {"applied", "cancelled", "failed"}:
        return action
    if action.generation_task_id:
        task = (
            db.query(GenerationTask)
            .filter(GenerationTask.id == action.generation_task_id)
            .first()
        )
        if task and task.status not in {"succeeded", "failed", "cancelled"}:
            request_cancel(db, task)
    action.status = "cancelled"
    action.stage = "cancelled"
    action.cancelled_at = utcnow()
    return action


def _asset_action_binding_manifest(
    db: Session,
    *,
    base: VNGraphRevision,
    action: AssetAction,
    target: dict[str, Any],
    result_graph_hash: str,
) -> dict[str, Any]:
    if (
        content_hash(base.binding_manifest or {}) != base.binding_manifest_hash
        or base.source_manifest_hash != base.binding_manifest_hash
    ):
        raise HTTPException(
            status_code=409,
            detail="基础 VNGraph revision 的资源清单已损坏",
        )
    manifest = json.loads(json.dumps(base.binding_manifest or {}))
    if not isinstance(manifest, dict):
        manifest = {}
    bindings = list(manifest.get("asset_bindings") or [])
    for order_index, version_id in enumerate(action.result_asset_version_ids or []):
        row = (
            db.query(AssetVersion, Asset, StorageObject)
            .join(Asset, Asset.id == AssetVersion.asset_id)
            .outerjoin(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
            .filter(
                AssetVersion.id == version_id,
                Asset.project_id == action.project_id,
            )
            .one_or_none()
        )
        if not row:
            raise HTTPException(
                status_code=409,
                detail="VNGraph patch 引用的素材版本不存在",
            )
        version, asset, storage = row
        reference = asset_version_reference(version, storage)
        try:
            validate_asset_version_reference(
                db,
                reference,
                expected_project_id=action.project_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        ready = bool(
            storage and storage.status == "active" and storage.deleted_at is None
        )
        bindings.append(
            {
                "resource_slot_id": f"asset-action:{action.id}:{order_index}",
                "binding_id": f"asset-action:{action.id}:{order_index}",
                "source_kind": "vn_graph_revision",
                "source_id": base.id,
                "node_key": target.get("node_key"),
                "segment_key": None,
                "scene_id": target.get("node_key"),
                "paragraph_id": None,
                "character_id": asset.character_id,
                "role": target.get("role") or asset.asset_type,
                "order_index": order_index,
                "required": True,
                "slot_status": "bound",
                **reference,
                "media_url": _media_url(storage.id) if ready else None,
                "legacy_url": asset.image_url,
                "asset_type": asset.asset_type,
                "target_name": asset.target_name,
                "emotion": asset.emotion,
                "status": "ready" if ready else "missing_media",
            }
        )
    manifest.update(
        {
            "manifest_version": "vngraph-binding-derived-v1",
            "project_id": base.project_id,
            "chapter_index": base.chapter_index,
            "chapter_revision": (
                manifest.get("chapter_revision") or {"id": base.chapter_revision_id}
            ),
            "script_revision": (
                manifest.get("script_revision") or {"id": base.script_revision_id}
            ),
            "asset_bindings": bindings,
            "voice_line_versions": list(manifest.get("voice_line_versions") or []),
            "versions": (
                manifest.get("versions")
                or {
                    "schema": base.schema_version,
                    "compiler": base.compiler_version,
                    "tachi_policy": base.tachi_policy_version,
                }
            ),
            "derivation": {
                "kind": "asset_action",
                "asset_action_id": action.id,
                "parent_revision_id": base.id,
                "parent_graph_hash": base.graph_hash,
                "patch": json.loads(json.dumps(action.vngraph_patch or [])),
                "patch_hash": content_hash(action.vngraph_patch or []),
                "result_graph_hash": result_graph_hash,
            },
        }
    )
    return manifest


def _persist_confirmed_graph(
    db: Session,
    *,
    action: AssetAction,
    graph_json: dict[str, Any],
    result_graph_hash: str | None,
) -> VNGraphRevision:
    target = dict(action.target or {})
    revision_id = target.get("graph_revision_id")
    if not revision_id:
        raise HTTPException(
            status_code=422,
            detail="确认 VNGraph patch 缺少 graph_revision_id",
        )
    base = (
        db.query(VNGraphRevision)
        .filter(
            VNGraphRevision.id == revision_id,
            VNGraphRevision.project_id == action.project_id,
        )
        .first()
    )
    if not base or not base.script_revision_id:
        raise HTTPException(status_code=404, detail="基础 VNGraph revision 不存在")
    chapter_index = base.chapter_index
    if base.status not in {"ready", "complete"}:
        raise HTTPException(status_code=409, detail="基础 VNGraph revision 尚未就绪")
    head = (
        db.query(VNGraphHead)
        .filter(
            VNGraphHead.script_revision_id == base.script_revision_id,
        )
        .with_for_update()
        .first()
    )
    current_hash = None
    if head:
        current = (
            db.query(VNGraphRevision)
            .filter(VNGraphRevision.id == head.current_revision_id)
            .first()
        )
        current_hash = current.graph_hash if current else None
    if not head or head.current_revision_id != base.id or current_hash != action.base_graph_hash:
        raise HTTPException(
            status_code=409,
            detail={"code": "graph.version_conflict", "current": current_hash},
        )
    if content_hash(base.graph_json) != base.graph_hash:
        raise HTTPException(
            status_code=409,
            detail={"code": "vngraph.derivation_invalid", "message": "基础 VNGraph 已损坏"},
        )
    try:
        replayed_graph = replay_asset_action_patch(
            base.graph_json,
            action.vngraph_patch or [],
        )
    except VNGraphDerivationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "vngraph.derivation_invalid", "message": str(exc)},
        ) from exc
    if graph_json != replayed_graph:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "vngraph.derivation_invalid",
                "message": "提交的 VNGraph 与服务端重放 patch 的结果不一致",
            },
        )
    computed_hash = content_hash(replayed_graph)
    if result_graph_hash and result_graph_hash != computed_hash:
        raise HTTPException(status_code=409, detail="result_graph_hash 与 graph_json 不一致")
    frozen_manifest = _asset_action_binding_manifest(
        db,
        base=base,
        action=action,
        target=target,
        result_graph_hash=computed_hash,
    )
    try:
        validate_derived_vn_graph(
            graph=replayed_graph,
            manifest=frozen_manifest,
            parent_graph=base.graph_json,
            patch=action.vngraph_patch or [],
        )
    except VNGraphDerivationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "vngraph.derivation_invalid", "message": str(exc)},
        ) from exc
    frozen_manifest_hash = content_hash(frozen_manifest)
    existing = (
        db.query(VNGraphRevision)
        .filter(
            VNGraphRevision.script_revision_id == base.script_revision_id,
            VNGraphRevision.graph_hash == computed_hash,
            VNGraphRevision.binding_manifest_hash == frozen_manifest_hash,
        )
        .first()
    )
    if existing:
        revision = existing
    else:
        revision_no = int(
            db.query(func.max(VNGraphRevision.revision_no))
            .filter(VNGraphRevision.script_revision_id == base.script_revision_id)
            .scalar()
            or 0
        ) + 1
        revision = VNGraphRevision(
            project_id=action.project_id,
            chapter_index=chapter_index,
            chapter_revision_id=base.chapter_revision_id,
            script_revision_id=base.script_revision_id,
            parent_revision_id=base.id,
            revision_no=revision_no,
            binding_manifest=frozen_manifest,
            binding_manifest_hash=frozen_manifest_hash,
            source_manifest_hash=frozen_manifest_hash,
            graph_hash=computed_hash,
            graph_json=replayed_graph,
            schema_version=base.schema_version,
            compiler_version=base.compiler_version,
            tachi_policy_version=base.tachi_policy_version,
            status="complete",
            generation_task_id=action.generation_task_id,
        )
        db.add(revision)
        db.flush()
    head.current_revision_id = revision.id
    head.lock_version += 1
    return revision


def confirm_asset_action(
    db: Session,
    *,
    action: AssetAction,
    graph_json: dict[str, Any] | None,
    result_graph_hash: str | None,
) -> AssetAction:
    refresh_asset_action(db, action)
    if action.status == "applied":
        return action
    if action.status != "awaiting_confirmation":
        raise HTTPException(status_code=409, detail="素材操作当前不可确认")
    target = dict(action.target or {})
    binding_source_kind = target["source_kind"]
    binding_source_id = target["source_id"]
    if target["source_kind"] == "vn_graph_revision":
        if graph_json is None:
            raise HTTPException(status_code=422, detail="确认 VNGraph patch 必须提交预览后的 graph_json")
        revision = _persist_confirmed_graph(
            db,
            action=action,
            graph_json=graph_json,
            result_graph_hash=result_graph_hash,
        )
        binding_source_id = revision.id
        action.result_graph_hash = revision.graph_hash
    for order_index, version_id in enumerate(action.result_asset_version_ids or []):
        version = db.query(AssetVersion).filter(AssetVersion.id == version_id).first()
        if not version:
            raise HTTPException(status_code=409, detail="素材版本不存在")
        slot = None
        resource_slot_id = str(target.get("resource_slot_id") or "").strip()
        if resource_slot_id and target["source_kind"] == "chapter_script_revision":
            slot = (
                db.query(ScriptResourceSlot)
                .filter(
                    ScriptResourceSlot.id == resource_slot_id,
                    ScriptResourceSlot.project_id == action.project_id,
                    ScriptResourceSlot.chapter_script_revision_id == binding_source_id,
                )
                .with_for_update()
                .first()
            )
            if not slot:
                raise HTTPException(status_code=404, detail="Script resource slot 不存在")
            if slot.asset_id is not None and version.asset_id != slot.asset_id:
                raise HTTPException(status_code=409, detail="素材版本不属于该预分配槽位")
        create_asset_binding(
            db,
            project_id=action.project_id,
            source_kind=binding_source_kind,
            source_id=binding_source_id,
            asset_version_id=version.id,
            node_key=target.get("node_key"),
            segment_key=target.get("segment_key"),
            role=target.get("role") or "background",
            order_index=slot.order_index if slot else order_index,
            required=slot.required if slot else True,
        )
        if slot:
            previous_binding = (slot.asset_version_id, slot.status)
            slot.asset_version_id = version.id
            slot.generation_task_id = action.generation_task_id
            slot.status = "bound"
            if previous_binding != (slot.asset_version_id, slot.status):
                slot.lock_version += 1
                slot.updated_at = utcnow()
    action.status = "applied"
    action.stage = "applied"
    action.progress = 100
    action.confirmed_at = utcnow()
    return action


def _reading_chapter_number(db: Session, reading: ReadingSession) -> int:
    snapshot = (
        db.query(StateSnapshot)
        .filter(
            StateSnapshot.id == reading.state_snapshot_id,
            StateSnapshot.project_id == reading.project_id,
        )
        .first()
        if reading.state_snapshot_id
        else None
    )
    state = dict(snapshot.state_json or {}) if snapshot else {}
    raw_value = state.get("chapter_number", state.get("chapter"))
    try:
        chapter_number = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="阅读会话缺少有效章节编号") from exc
    if chapter_number < 1:
        raise HTTPException(status_code=409, detail="阅读会话章节编号无效")
    return chapter_number


def _state_with_continuation_point(state_json: dict[str, Any] | None) -> dict[str, Any]:
    """Expose the narrative tail separately from commentary and earlier chapter text."""
    state = dict(state_json or {})
    if state.get("chapter_continuation_point"):
        return state
    context = str(state.get("chapter_context") or "")
    if not context:
        return state
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", context)
        if paragraph.strip() and not re.match(r"^〚\d+〛", paragraph.strip())
    ]
    if paragraphs:
        state["chapter_continuation_point"] = "\n\n".join(paragraphs[-6:])
    return state


def select_public_continuation(
    db: Session,
    *,
    session_id: str,
    user_id: int,
    continuation_id: str,
) -> ReadingSession:
    reading = get_owned_session(db, session_id, user_id, lock=True)
    chapter_number = _reading_chapter_number(db, reading)
    continuation = (
        db.query(ReadingContinuation)
        .filter(
            ReadingContinuation.id == continuation_id,
            ReadingContinuation.release_id == reading.release_id,
            ReadingContinuation.chapter_number == chapter_number,
            ReadingContinuation.status == "confirmed",
        )
        .first()
    )
    if not continuation:
        raise HTTPException(status_code=404, detail="公共故事节点不存在")
    if reading.selected_continuation_id != continuation.id:
        reading.selected_continuation_id = continuation.id
        reading.lock_version += 1
        reading.updated_at = utcnow()
    return reading


def _public_continuation_chain(
    db: Session,
    *,
    continuation_id: str | None,
    release_id: str,
    chapter_number: int,
    limit: int = 12,
) -> list[ReadingContinuation]:
    chain: list[ReadingContinuation] = []
    seen: set[str] = set()
    current_id = continuation_id
    while current_id and len(chain) < limit and current_id not in seen:
        seen.add(current_id)
        current = (
            db.query(ReadingContinuation)
            .filter(
                ReadingContinuation.id == current_id,
                ReadingContinuation.release_id == release_id,
                ReadingContinuation.chapter_number == chapter_number,
                ReadingContinuation.status == "confirmed",
            )
            .first()
        )
        if not current:
            break
        chain.append(current)
        current_id = current.parent_continuation_id
    chain.reverse()
    return chain


def _recent_library_selection_context(
    db: Session,
    chain: list[ReadingContinuation],
    *,
    limit: int = 12,
) -> tuple[list[str], list[str]]:
    """Return recent catalog/file identities ordered oldest-to-newest."""

    version_ids = [
        str(version_id)
        for continuation in chain
        for version_id in (continuation.frozen_asset_version_ids or [])
        if version_id
    ][-limit:]
    if not version_ids:
        return [], []
    rows = (
        db.query(AssetVersion, Asset)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .filter(AssetVersion.id.in_(version_ids))
        .all()
    )
    by_version_id = {version.id: (version, asset) for version, asset in rows}
    library_asset_ids: list[str] = []
    storage_object_ids: list[str] = []
    for version_id in version_ids:
        row = by_version_id.get(version_id)
        if not row:
            continue
        version, asset = row
        params = asset.generation_params if isinstance(asset.generation_params, dict) else {}
        v2_meta = params.get("v2") if isinstance(params.get("v2"), dict) else {}
        library_asset_id = str(v2_meta.get("library_asset_id") or "").strip()
        if library_asset_id:
            library_asset_ids.append(library_asset_id)
        if version.storage_object_id:
            storage_object_ids.append(str(version.storage_object_id))
    return library_asset_ids, storage_object_ids


def _continuation_chain_context(chain: list[ReadingContinuation]) -> dict[str, Any] | None:
    if not chain:
        return None
    return {
        "selected_node_id": chain[-1].id,
        "ancestry": [
            {
                "id": item.id,
                "direction": item.direction,
                "continuation_text": item.continuation_text,
                "state_delta": dict(item.state_delta or {}),
            }
            for item in chain
        ],
    }


def create_reading_continuation(
    db: Session,
    *,
    session_id: str,
    user_id: int,
    direction: str,
    visual_mode: str,
    uploaded_asset_version_ids: list[str],
    max_generated_assets: int,
    parent_continuation_id: str | None,
    idempotency_key: str,
    catalog_version: str,
    matcher_version: str,
) -> ReadingContinuation:
    key = _validate_idempotency_key(idempotency_key)
    reading = get_owned_session(db, session_id, user_id)
    if reading.status != "active":
        raise HTTPException(status_code=409, detail="阅读会话当前不可续写")
    chapter_number = _reading_chapter_number(db, reading)
    digest = _request_hash(
        {
            "direction": direction,
            "visual_mode": visual_mode,
            "uploaded_asset_version_ids": uploaded_asset_version_ids,
            "max_generated_assets": max_generated_assets,
            "parent_continuation_id": parent_continuation_id,
        }
    )
    existing = (
        db.query(ReadingContinuation)
        .filter(
            ReadingContinuation.session_id == session_id,
            ReadingContinuation.idempotency_key == key,
        )
        .first()
    )
    if existing:
        if existing.request_hash != digest:
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求")
        return existing
    if parent_continuation_id:
        parent = (
            db.query(ReadingContinuation)
            .filter(
                ReadingContinuation.id == parent_continuation_id,
                ReadingContinuation.release_id == reading.release_id,
                ReadingContinuation.chapter_number == chapter_number,
                ReadingContinuation.status == "confirmed",
            )
            .first()
        )
        if not parent:
            raise HTTPException(status_code=404, detail="公共故事节点不存在或尚未确认")
    reading.selected_continuation_id = parent_continuation_id
    if visual_mode == "user_upload":
        if not uploaded_asset_version_ids:
            raise HTTPException(status_code=422, detail="user_upload 必须提供素材版本")
        versions = (
            db.query(AssetVersion)
            .join(Asset, Asset.id == AssetVersion.asset_id)
            .filter(
                AssetVersion.id.in_(uploaded_asset_version_ids),
                Asset.project_id == reading.project_id,
            )
            .all()
        )
        if len(versions) != len(set(uploaded_asset_version_ids)):
            raise HTTPException(status_code=404, detail="上传素材版本不存在")
    continuation = ReadingContinuation(
        session_id=session_id,
        release_id=reading.release_id,
        chapter_number=chapter_number,
        parent_continuation_id=parent_continuation_id,
        parent_node_id=reading.head_node_id,
        direction=direction,
        visual_mode=visual_mode,
        status="processing",
        continuation_text="",
        state_delta={},
        scene_manifest_id=None,
        vngraph_patch=[],
        uploaded_asset_version_ids=uploaded_asset_version_ids,
        frozen_asset_version_ids=[],
        base_session_lock_version=reading.lock_version,
        idempotency_key=key,
        request_hash=digest,
    )
    db.add(continuation)
    db.flush()
    task, _ = create_generation_task(
        db,
        user_id=user_id,
        project_id=reading.project_id,
        kind=CONTINUATION_TASK_KIND,
        idempotency_key=f"reading-continuation:{session_id}:{key}",
        source_refs={
            "continuation_id": continuation.id,
            "session_id": session_id,
            "release_id": reading.release_id,
            "head_node_id": reading.head_node_id,
            "state_snapshot_id": reading.state_snapshot_id,
            "base_session_lock_version": reading.lock_version,
        },
        parameters={
            "direction": direction,
            "visual_mode": visual_mode,
            "uploaded_asset_version_ids": uploaded_asset_version_ids,
            "max_generated_assets": max_generated_assets,
            "parent_continuation_id": parent_continuation_id,
            "catalog_version": catalog_version,
            "matcher_version": matcher_version,
            "prompt_version": CONTINUATION_PROMPT_VERSION,
        },
        estimated_cost=CONTINUATION_CREDIT_COST,
        enqueue_event_type=CONTINUATION_EVENT,
    )
    continuation.generation_task_id = task.id
    return continuation


def prepare_direction_suggestion_request(
    db: Session,
    *,
    session_id: str,
    user_id: int,
) -> DirectionSuggestionLLMRequest:
    reading = get_owned_session(db, session_id, user_id)
    if reading.status != "active":
        raise HTTPException(status_code=409, detail="阅读会话当前不可续写")
    snapshot = (
        db.query(StateSnapshot)
        .filter(
            StateSnapshot.id == reading.state_snapshot_id,
            StateSnapshot.project_id == reading.project_id,
        )
        .first()
        if reading.state_snapshot_id
        else None
    )
    head = (
        db.query(StoryNode)
        .filter(
            StoryNode.id == reading.head_node_id,
            StoryNode.project_id == reading.project_id,
        )
        .first()
        if reading.head_node_id
        else None
    )
    chapter_number = _reading_chapter_number(db, reading)
    chain = _public_continuation_chain(
        db,
        continuation_id=reading.selected_continuation_id,
        release_id=reading.release_id,
        chapter_number=chapter_number,
    )
    state = _state_with_continuation_point(snapshot.state_json if snapshot else None)
    head_context = {
        "id": head.id if head else None,
        "node_type": head.node_type if head else None,
        "payload": dict(head.payload or {}) if head else {},
    }
    latest_context = _continuation_chain_context(chain)
    for label, value, limit in (
        ("state", state, 64 * 1024),
        ("head", head_context, 32 * 1024),
        ("latest", latest_context, 96 * 1024),
    ):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > limit:
            raise HTTPException(status_code=413, detail=f"{label} 续写方向上下文过大")
    source_hash = content_hash(
        {
            "session_id": reading.id,
            "lock_version": reading.lock_version,
            "state": state,
            "head": head_context,
            "latest": latest_context,
        }
    )
    return DirectionSuggestionLLMRequest(
        source_hash=source_hash,
        state=state,
        head_context=head_context,
        latest_continuation=latest_context,
    )


def prepare_continuation_generation_request(
    db: Session, task: GenerationTask
) -> ContinuationLLMRequest:
    if task.kind != CONTINUATION_TASK_KIND:
        raise ValueError("task is not a reading continuation task")
    refs = dict(task.source_refs or {})
    params = dict(task.parameters or {})
    continuation = (
        db.query(ReadingContinuation)
        .filter(
            ReadingContinuation.id == refs.get("continuation_id"),
            ReadingContinuation.generation_task_id == task.id,
            ReadingContinuation.status == "processing",
        )
        .first()
    )
    reading = (
        db.query(ReadingSession)
        .filter(
            ReadingSession.id == refs.get("session_id"),
            ReadingSession.release_id == refs.get("release_id"),
        )
        .first()
    )
    try:
        release = get_active_public_release(
            db,
            release_id=str(refs.get("release_id") or ""),
        )
    except AppError:
        release = None
    if not continuation or not reading or not release:
        raise ValueError("continuation source snapshot is missing")
    if release.project_id != reading.project_id:
        raise ValueError("continuation source snapshot is missing")
    if continuation.base_session_lock_version != int(refs.get("base_session_lock_version") or 0):
        raise ValueError("continuation source lock version changed")
    head = (
        db.query(StoryNode)
        .filter(
            StoryNode.id == refs.get("head_node_id"),
            StoryNode.project_id == reading.project_id,
        )
        .first()
        if refs.get("head_node_id")
        else None
    )
    snapshot = (
        db.query(StateSnapshot)
        .filter(
            StateSnapshot.id == refs.get("state_snapshot_id"),
            StateSnapshot.project_id == reading.project_id,
        )
        .first()
        if refs.get("state_snapshot_id")
        else None
    )
    parent_chain = _public_continuation_chain(
        db,
        continuation_id=continuation.parent_continuation_id,
        release_id=reading.release_id,
        chapter_number=continuation.chapter_number or _reading_chapter_number(db, reading),
    )
    story = (release.manifest_json or {}).get("story") or {}
    frozen_head = next(
        (
            node
            for node in story.get("nodes", [])
            if isinstance(node, dict) and node.get("id") == refs.get("head_node_id")
        ),
        None,
    )
    release_context = {
        "release_id": release.id,
        "release_version": release.version,
        "manifest_hash": release.manifest_hash,
        "bible_revision_id": release.bible_revision_id,
        "outline_revision_id": release.outline_revision_id,
        "frozen_head": frozen_head,
    }
    head_context = {
        "id": head.id if head else None,
        "node_type": head.node_type if head else None,
        "payload": dict(head.payload or {}) if head else {},
    }
    state = _state_with_continuation_point(snapshot.state_json if snapshot else None)
    parent_context = _continuation_chain_context(parent_chain)
    for label, value, limit in (
        ("release", release_context, 64 * 1024),
        ("head", head_context, 32 * 1024),
        ("state", state, 64 * 1024),
        ("parent", parent_context, 96 * 1024),
    ):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > limit:
            raise ValueError(f"{label} continuation context exceeds its limit")
    source_hash = content_hash(
        {
            "continuation_id": continuation.id,
            "release": release_context,
            "head": head_context,
            "state": state,
            "parent": parent_context,
            "direction": params.get("direction"),
            "prompt_version": params.get("prompt_version"),
        }
    )
    return ContinuationLLMRequest(
        source_hash=source_hash,
        direction=str(params.get("direction") or ""),
        release_context=release_context,
        head_context=head_context,
        state=state,
        parent_continuation=parent_context,
    )


def persist_generated_continuation(
    db: Session,
    *,
    task: GenerationTask,
    generated: GeneratedContinuation,
) -> ReadingContinuation:
    refs = dict(task.source_refs or {})
    params = dict(task.parameters or {})
    continuation = (
        db.query(ReadingContinuation)
        .filter(ReadingContinuation.id == refs.get("continuation_id"))
        .with_for_update()
        .first()
    )
    if not continuation or continuation.generation_task_id != task.id:
        raise ValueError("continuation task result target is missing")
    if continuation.status == "preview_ready":
        return continuation
    if continuation.status != "processing":
        raise ValueError("continuation is no longer processing")
    reading = db.query(ReadingSession).filter(ReadingSession.id == continuation.session_id).first()
    if not reading:
        raise ValueError("reading session is missing")

    continuation.continuation_text = generated.continuation_text
    continuation.state_delta = generated.state_delta
    text_patch = {
        "op": "add_continuation_text",
        "text": generated.continuation_text,
        "state_delta": generated.state_delta,
    }
    max_generated_assets = max(0, int(params.get("max_generated_assets") or 0))
    if continuation.visual_mode == "system_generate" and max_generated_assets == 0:
        # Text-only continuation deliberately keeps the existing DB enum and
        # task contract.  It simply skips the optional visual materialization.
        continuation.vngraph_patch = [text_patch]
    elif continuation.visual_mode == "user_upload":
        versions = (
            db.query(AssetVersion)
            .join(Asset, Asset.id == AssetVersion.asset_id)
            .filter(
                AssetVersion.id.in_(continuation.uploaded_asset_version_ids or []),
                Asset.project_id == reading.project_id,
            )
            .all()
        )
        versions = [_attach_asset(db, version) for version in versions]
        if not versions:
            raise ValueError("continuation upload versions are missing")
        target = {
            "source_kind": "reading_continuation",
            "source_id": continuation.id,
            "node_key": "continuation-preview",
            "asset_slot": "BackgroundImage",
        }
        scene = _create_scene_manifest(
            db,
            project_id=reading.project_id,
            reading_session_id=reading.id,
            versions=versions,
            target=target,
            matches=[],
            catalog_version=None,
            matcher_version=str(params.get("matcher_version") or MATCHER_VERSION),
        )
        continuation.scene_manifest_id = scene.id
        continuation.frozen_asset_version_ids = [version.id for version in versions]
        continuation.vngraph_patch = [text_patch, *_build_patch(target, versions)]
    else:
        intent = generated.scene_intent
        catalog_version = str(params.get("catalog_version") or "v1.1")
        matcher_version = str(params.get("matcher_version") or MATCHER_VERSION)
        recent_chain = _public_continuation_chain(
            db,
            continuation_id=continuation.parent_continuation_id,
            release_id=continuation.release_id,
            chapter_number=continuation.chapter_number,
        )
        recent_library_asset_ids, recent_storage_object_ids = _recent_library_selection_context(
            db,
            recent_chain,
        )
        direction_context = continuation.direction.strip()[:1200]
        background_query = "\n".join(
            part
            for part in (
                intent.background.strip(),
                f"用户选择的故事发展方向：{direction_context}" if direction_context else "",
                " ".join(_flatten_strings(intent.taxonomy or {})),
            )
            if part
        )
        background_generation_prompt = _build_empty_environment_generation_prompt(
            background=intent.background,
            taxonomy=dict(intent.taxonomy or {}),
            style=intent.style,
        )
        target = {
            "source_kind": "reading_continuation",
            "source_id": continuation.id,
            "node_key": "continuation-preview",
            "asset_slot": "BackgroundImage",
            "role": "background",
        }
        background_matches, _ = search_library_assets(
            db,
            catalog_version=catalog_version,
            matcher_version=matcher_version,
            query=background_query,
            asset_type="background",
            identity_group=None,
            limit=1,
            offset=0,
            required_facets={
                key: value
                for key, value in dict(intent.taxonomy or {}).items()
                if key in FACET_CATEGORIES
            },
            preferred_style=intent.style,
            recent_library_asset_ids=recent_library_asset_ids,
            recent_storage_object_ids=recent_storage_object_ids,
        )
        common_input = {
            "query": background_query,
            "description": intent.background,
            "prompt": background_generation_prompt,
            "asset_type": "background",
            "target_name": "游客续写场景图",
            "style": intent.style,
            "taxonomy": intent.taxonomy,
            "preferred_style": intent.style,
            "required_facets": {
                key: value
                for key, value in dict(intent.taxonomy or {}).items()
                if key in FACET_CATEGORIES
            },
            "max_assets": 1,
            "generation_prompt": background_generation_prompt,
            "negative_prompt": (
                "person, people, human, man, woman, character, face, crowd, portrait, "
                "silhouette, soldier, guard, troop, figure, text, lettering, caption, watermark"
            ),
            "negative_prompt_version": "visual-novel-empty-environment-v2",
            "generation_prompt_version": "visual-novel-empty-environment-v2",
            "generation_source_policy": (
                "sanitized_scene_environment_and_safe_taxonomy_without_raw_direction"
            ),
            "generate_on_low_confidence": True,
            "recent_library_asset_ids": recent_library_asset_ids,
            "recent_storage_object_ids": recent_storage_object_ids,
            "selection_principle": "current_scene_semantics_then_soft_repeat_penalty",
            "direction_context": direction_context,
        }
        # Hard taxonomy filters can legitimately leave the catalog with no
        # candidate. That is not a continuation failure: keep the text preview
        # usable and queue a real generated background instead.
        action = create_asset_action(
            db,
            user_id=reading.user_id,
            project_id=reading.project_id,
            mode="agent_compose" if background_matches else "direct_generate",
            target=target,
            input_data=common_input,
            idempotency_key=f"continuation-assets:{continuation.id}",
            catalog_version=catalog_version,
            matcher_version=matcher_version,
            agent_enabled=True,
            system_generate=True,
            reading_session_id=reading.id,
        )
        companion_versions: list[AssetVersion] = []
        character_matches: list[dict[str, Any]] = []
        used_companion_library_ids: set[str] = set()
        remaining = max(0, max_generated_assets - 1)
        for character in intent.characters[:remaining]:
            identity_group = str(character.identity_group or "").strip()
            # A portrait without a stable identity group can silently become a
            # different person. Keep the stage background-only until the
            # provider or character-profile layer supplies a real identity.
            if not identity_group:
                continue
            portrait_query = " ".join(
                filter(
                    None,
                    [
                        character.name,
                        character.description,
                        character.expression,
                        character.pose,
                        direction_context,
                    ],
                )
            )
            exact_filters = {
                key: value
                for key, value in {
                    "expression": character.expression,
                    "pose": character.pose,
                }.items()
                if value
            }
            relaxed_filters: list[dict[str, Any]] = [exact_filters]
            if character.expression and character.pose:
                relaxed_filters.append({"expression": character.expression})
            relaxed_filters.append({})

            matches: list[dict[str, Any]] = []
            seen_filter_keys: set[str] = set()
            for portrait_filters in relaxed_filters:
                filter_key = json.dumps(portrait_filters, ensure_ascii=False, sort_keys=True)
                if filter_key in seen_filter_keys:
                    continue
                seen_filter_keys.add(filter_key)
                matches, _ = search_library_assets(
                    db,
                    catalog_version=catalog_version,
                    matcher_version=matcher_version,
                    query=portrait_query,
                    asset_type="portrait",
                    identity_group=identity_group,
                    limit=5,
                    offset=0,
                    required_facets=portrait_filters,
                    preferred_style=intent.style,
                    recent_library_asset_ids=recent_library_asset_ids,
                    recent_storage_object_ids=recent_storage_object_ids,
                )
                matches = [
                    match
                    for match in matches
                    if str(match["id"]) not in used_companion_library_ids
                ]
                if matches:
                    break
            if not matches:
                continue
            match = matches[0]
            used_companion_library_ids.add(str(match["id"]))
            recent_library_asset_ids.append(str(match["id"]))
            recent_storage_object_ids.append(str(match["storage_object_id"]))
            library_asset, storage = get_library_asset(db, match["id"])
            companion_versions.append(
                materialize_library_asset(
                    db,
                    project_id=reading.project_id,
                    library_asset=library_asset,
                    storage=storage,
                    source_kind="reading_continuation",
                    source_id=continuation.id,
                )
            )
            character_matches.append(match)
        base_versions = [
            db.query(AssetVersion).filter(AssetVersion.id == version_id).one()
            for version_id in (action.result_asset_version_ids or [])
        ]
        combined_versions = [*base_versions, *companion_versions]
        if companion_versions:
            action.input = {
                **dict(action.input or {}),
                "companion_asset_version_ids": [version.id for version in companion_versions],
            }
            # A direct-generated background has no version until its render
            # task finishes. Keep portrait companions attached to the action;
            # refresh_asset_action will finalize all slots together later.
            if base_versions:
                scene = (
                    db.query(SceneManifest)
                    .filter(SceneManifest.id == action.scene_manifest_id)
                    .first()
                )
                _finalize_action_with_versions(
                    db,
                    action=action,
                    versions=combined_versions,
                    matches=[
                        *((scene.match_summary or {}).get("items", []) if scene else []),
                        *character_matches,
                    ],
                    catalog_version=catalog_version,
                    matcher_version=matcher_version,
                )
        continuation.asset_action_id = action.id
        continuation.scene_manifest_id = action.scene_manifest_id
        continuation.frozen_asset_version_ids = list(action.result_asset_version_ids or [])
        continuation.vngraph_patch = [text_patch, *list(action.vngraph_patch or [])]
    continuation.status = "preview_ready"
    return continuation


def get_owned_continuation(
    db: Session, continuation_id: str, session_id: str, user_id: int
) -> ReadingContinuation:
    get_owned_session(db, session_id, user_id)
    continuation = (
        db.query(ReadingContinuation)
        .filter(
            ReadingContinuation.id == continuation_id,
            ReadingContinuation.session_id == session_id,
        )
        .first()
    )
    if not continuation:
        raise HTTPException(status_code=404, detail="阅读续写不存在")
    return continuation


def _sync_continuation_asset_action(
    db: Session, continuation: ReadingContinuation
) -> AssetAction | None:
    if not continuation.asset_action_id:
        return None
    action = (
        db.query(AssetAction)
        .filter(AssetAction.id == continuation.asset_action_id)
        .first()
    )
    if not action:
        return None
    refresh_asset_action(db, action)
    if list(action.result_asset_version_ids or []) != list(
        continuation.frozen_asset_version_ids or []
    ):
        text_patch = [
            item
            for item in (continuation.vngraph_patch or [])
            if isinstance(item, dict) and item.get("op") == "add_continuation_text"
        ]
        continuation.scene_manifest_id = action.scene_manifest_id
        continuation.frozen_asset_version_ids = list(action.result_asset_version_ids or [])
        continuation.vngraph_patch = [*text_patch, *list(action.vngraph_patch or [])]
    if continuation.status == "confirmed":
        _publish_continuation_assets(db, continuation)
    return action


def _publish_continuation_assets(db: Session, continuation: ReadingContinuation) -> None:
    version_ids = list(continuation.frozen_asset_version_ids or [])
    if not version_ids:
        return
    rows = (
        db.query(AssetVersion, StorageObject)
        .join(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(AssetVersion.id.in_(version_ids))
        .all()
    )
    for version, storage in rows:
        rights = dict(version.rights_metadata or {})
        if rights.get("origin") == "upload" and not rights.get("rights_attested"):
            continue
        storage.status = "active"
        if storage.visibility != "public":
            storage.visibility = "release"


def continuation_asset_items(
    db: Session, continuation: ReadingContinuation
) -> list[dict[str, Any]]:
    version_ids = list(continuation.frozen_asset_version_ids or [])
    if not version_ids:
        return []
    rows = (
        db.query(AssetVersion, Asset, StorageObject)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .join(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(
            AssetVersion.id.in_(version_ids),
            StorageObject.status == "active",
            StorageObject.visibility.in_(("release", "public")),
        )
        .all()
    )
    by_id = {version.id: (version, asset, storage) for version, asset, storage in rows}
    assets: list[dict[str, Any]] = []
    for version_id in version_ids:
        row = by_id.get(version_id)
        if not row:
            continue
        version, asset, storage = row
        params = asset.generation_params if isinstance(asset.generation_params, dict) else {}
        taxonomy = dict((params.get("v2") or {}).get("taxonomy") or {})
        assets.append(
            {
                "asset_id": asset.id,
                "asset_version_id": version.id,
                "storage_object_id": storage.id,
                "asset_type": asset.asset_type,
                "media_url": _media_url(storage.id),
                "sha256": storage.sha256,
                "description_cn": asset.description_cn or "",
                "tags": list(taxonomy.get("tags") or []),
                "taxonomy": taxonomy,
                "width": version.width,
                "height": version.height,
            }
        )
    return assets


def list_public_continuations(
    db: Session,
    *,
    project_id: int,
    release_id: str,
    chapter_number: int,
) -> dict[str, Any]:
    try:
        release = get_active_public_release(db, release_id=release_id)
    except AppError as exc:
        raise HTTPException(status_code=404, detail="公开发布版本不存在") from exc
    if release.project_id != project_id:
        raise HTTPException(status_code=404, detail="公开发布版本不存在")
    rows = (
        db.query(ReadingContinuation)
        .filter(
            ReadingContinuation.release_id == release_id,
            ReadingContinuation.chapter_number == chapter_number,
            ReadingContinuation.status == "confirmed",
        )
        .order_by(ReadingContinuation.confirmed_at, ReadingContinuation.created_at)
        .all()
    )
    ids = [item.id for item in rows]
    selection_counts = {
        continuation_id: count
        for continuation_id, count in (
            db.query(ReadingSession.selected_continuation_id, func.count(ReadingSession.id))
            .filter(ReadingSession.selected_continuation_id.in_(ids))
            .group_by(ReadingSession.selected_continuation_id)
            .all()
            if ids
            else []
        )
    }
    by_id = {item.id: item for item in rows}

    def depth(item: ReadingContinuation) -> int:
        result = 0
        parent_id = item.parent_continuation_id
        seen = {item.id}
        while parent_id and parent_id in by_id and parent_id not in seen:
            seen.add(parent_id)
            result += 1
            parent_id = by_id[parent_id].parent_continuation_id
        return result

    return {
        "project_id": project_id,
        "release_id": release_id,
        "chapter_number": chapter_number,
        "nodes": [
            {
                "id": item.id,
                "parent_continuation_id": item.parent_continuation_id,
                "direction": item.direction,
                "continuation_text": item.continuation_text,
                "chapter_number": chapter_number,
                "depth": depth(item),
                "selection_count": int(selection_counts.get(item.id, 0)),
                "assets": continuation_asset_items(db, item),
                "confirmed_at": item.confirmed_at,
            }
            for item in rows
            if item.confirmed_at is not None
        ],
    }


def continuation_dict(db: Session, continuation: ReadingContinuation) -> dict[str, Any]:
    if continuation.status == "processing" and continuation.generation_task_id:
        task = (
            db.query(GenerationTask)
            .filter(GenerationTask.id == continuation.generation_task_id)
            .first()
        )
        if task and task.status in {"failed", "cancelled"}:
            continuation.status = "cancelled" if task.status == "cancelled" else "failed"
            continuation.error = {
                "code": task.error_code or f"task_{task.status}",
                "message": task.error_detail or "续写生成未完成",
            }
    if continuation.status in {"preview_ready", "confirmed"} and continuation.asset_action_id:
        _sync_continuation_asset_action(db, continuation)
    scene = (
        db.query(SceneManifest).filter(SceneManifest.id == continuation.scene_manifest_id).first()
        if continuation.scene_manifest_id
        else None
    )
    return {
        "id": continuation.id,
        "session_id": continuation.session_id,
        "parent_continuation_id": continuation.parent_continuation_id,
        "parent_node_id": continuation.parent_node_id,
        "direction": continuation.direction,
        "visual_mode": continuation.visual_mode,
        "status": continuation.status,
        "continuation_text": continuation.continuation_text,
        "state_delta": dict(continuation.state_delta or {}),
        "scene_manifest": scene_manifest_dict(scene),
        "vngraph_patch": list(continuation.vngraph_patch or []),
        "uploaded_asset_version_ids": list(continuation.uploaded_asset_version_ids or []),
        "frozen_asset_version_ids": list(continuation.frozen_asset_version_ids or []),
        "task_id": continuation.generation_task_id,
        "asset_action_id": continuation.asset_action_id,
        "base_session_lock_version": continuation.base_session_lock_version,
        "error": dict(continuation.error) if isinstance(continuation.error, dict) else None,
        "confirmed_at": continuation.confirmed_at,
        "created_at": continuation.created_at,
        "updated_at": continuation.updated_at,
    }


def confirm_reading_continuation(
    db: Session,
    *,
    continuation: ReadingContinuation,
    user_id: int,
    expected_lock_version: int,
) -> ReadingSession:
    reading = get_owned_session(db, continuation.session_id, user_id, lock=True)
    if continuation.status == "confirmed":
        return reading
    if continuation.status != "preview_ready":
        raise HTTPException(status_code=409, detail="续写当前不可确认")
    action = _sync_continuation_asset_action(db, continuation)
    if action and action.status == "processing":
        raise HTTPException(status_code=409, detail="场景图仍在生成，请稍后再采用续写")
    if reading.lock_version != expected_lock_version or (
        continuation.base_session_lock_version != expected_lock_version
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "session.version_conflict", "current": reading.lock_version},
        )
    continuation.status = "confirmed"
    continuation.confirmed_at = utcnow()
    reading.selected_continuation_id = continuation.id
    _publish_continuation_assets(db, continuation)
    # The released StoryNode and ProjectRelease remain immutable.  Only the
    # session overlay head/version advances; the frozen continuation row holds
    # the text, state delta, scene and asset versions.
    reading.lock_version += 1
    reading.updated_at = utcnow()
    return reading

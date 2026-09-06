"""
Background Scene Analyzer Service (Stage_Background_AR L3.01-L3.08)

输入：章节正文 + 章节大纲 + 故事设定（角色名/别名）
输出：List[BackgroundSceneSpec]

职责：
- ``enrich_segments``（推荐 / VNGraph 对齐）：以 SceneSegment 列表为权威张数与
  segment_id，LLM 只 enrich 环境描述；失败仍返回与 segments 等长的 specs。
- ``analyze_chapter``（遗留）：由 LLM 自行切场景，再事后贴 segment 指纹。
- 永远不输出剧情摘要 / 对白 / 角色动作

设计哲学：Docs/researches/Stage_Background_AR/00_philosophy.md
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.schemas import (
    BackgroundSceneSpec,
    PeoplePolicy,
    StyleTag,
)
from app.services.scene_segmenter_service import SceneSegmenterService, SceneSegment
from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available
from app.services.background_positive_text_sanitizer import (
    forbidden_story_names,
    sanitize_positive_background_text,
)
from app.services.text_llm_config import DEFAULT_TEXT_LLM_MODEL, resolve_request_model, text_llm_api_key, text_llm_base_url, text_llm_model

logger = logging.getLogger("bg_scene_analyzer")

# ----------------------- env -----------------------

ANALYZER_ENABLED = os.getenv("BG_ANALYZER_ENABLED", "true").lower() == "true"
ANALYZER_MODEL = text_llm_model("BG_ANALYZER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
ANALYZER_API_KEY = text_llm_api_key("BG_ANALYZER_API_KEY", "REWRITER_API_KEY")
ANALYZER_BASE_URL = text_llm_base_url("BG_ANALYZER_BASE_URL", "REWRITER_BASE_URL")
ANALYZER_TIMEOUT = float(os.getenv("BG_ANALYZER_TIMEOUT", "90.0"))
ANALYZER_TEMPERATURE = float(os.getenv("BG_ANALYZER_TEMPERATURE", "0.2"))
ANALYZER_MAX_TOKENS = int(os.getenv("BG_ANALYZER_MAX_TOKENS", "7000"))
ANALYZER_RETRIES = int(os.getenv("BG_ANALYZER_RETRIES", "1"))
ANALYZER_CONTENT_CHAR_LIMIT = int(os.getenv("BG_ANALYZER_CONTENT_CHAR_LIMIT", "8000"))
ANALYZER_RETRY_CONTENT_CHAR_LIMIT = int(os.getenv("BG_ANALYZER_RETRY_CONTENT_CHAR_LIMIT", "1800"))
ANALYZER_OUTLINE_CHAR_LIMIT = int(os.getenv("BG_ANALYZER_OUTLINE_CHAR_LIMIT", "1200"))
ANALYZER_RETRY_OUTLINE_CHAR_LIMIT = int(os.getenv("BG_ANALYZER_RETRY_OUTLINE_CHAR_LIMIT", "700"))
ANALYZER_MAX_SCENES = int(os.getenv("BG_ANALYZER_MAX_SCENES", "16"))
ANALYZER_USE_SEGMENTER_FALLBACK_LLM = (
    os.getenv("BG_ANALYZER_USE_SEGMENTER_FALLBACK_LLM", "false").lower() == "true"
)

CACHE_DIR = Path(os.getenv(
    "BG_ANALYZER_CACHE_DIR",
    str(Path(__file__).parent.parent.parent / ".cache" / "bg_scene_analyzer")
))
ANALYZER_RECIPE_VERSION = "content-visual-moment-v2"

# ----------------------- constants -----------------------

# L3.03: 剧情/对白/角色动作 黑名单（中文）— 用于 _validate_no_plot
PLOT_KEYWORDS_ZH = [
    # 对白引语
    "说道", "说：", "道：", "问道", "答道", "喝道", "喊道", "笑了笑道", "淡淡道",
    # 心理/动作
    "心想", "想了想", "决定", "决定要", "于是", "忽然", "突然",
    # 关系/冲突
    "对她", "对他", "两人", "二人", "敌视", "拥抱", "亲吻",
]

# L3.05: people_policy 推断关键词（与 image_generation_profiles.json 保持一致）
PUBLIC_ACTIVITY_SIGNALS = [
    "人群", "熙攘", "喧闹", "集市", "商贩", "叫卖", "宾客", "满座",
    "朝会", "群臣", "军阵", "阅兵", "巡逻", "商队", "船工", "挑夫",
    "行人", "路人", "市井",
]
EMPTY_SIGNALS = [
    "空无一人", "寂静", "无人", "空旷", "独自", "独坐", "空荡",
    "人去楼空", "荒废", "废墟", "深夜", "凌晨", "破晓", "杳无人迹",
]

BACKGROUND_UNSAFE_VISUAL_KEYWORDS = [
    # 静态人物词
    "人", "人物", "人群", "脸", "人脸", "面孔", "肖像", "照片", "合照", "群像",
    "背影", "身影", "剪影", "眼睛", "表情", "微笑", "哭", "笑", "四张脸",
    "显示器映出", "屏幕映出", "镜子映出", "倒映出", "工位摆满手办",
    "person", "people", "human", "face", "portrait", "photo", "group photo",
    "silhouette", "figure", "reflection of",
    # 人体部位（CogView-4 会按部位补全身）
    "手", "掌心", "手指", "手掌", "手臂", "胳膊", "脚", "腿", "头发", "皮肤",
    "hand", "palm", "finger", "arm", "foot", "leg", "hair", "skin",
    # 称谓/角色身份词
    "母亲", "父亲", "母亲掌心", "他", "她", "你", "我", "哥哥", "姐姐", "弟弟",
    "妹妹", "男人", "女人", "男孩", "女孩", "老师", "学生", "医生", "护士",
    "mother", "father", "he ", "she ", "you ", "man", "woman", "boy", "girl",
    # 动作动词（这些会让 CogView-4 按动作生成主体化人像）
    "托出", "托着", "捧着", "握着", "拿着", "举着", "指着", "抚摸", "抚摸着",
    "伸手", "伸出", "举起", "拿起", "放下", "转身", "走来", "走进", "走动", "显示出",
    "坐", "站", "蹲", "跪", "躺", "靠", "倚", "拥抱", "推", "拉", "抓",
    "holding", "carrying", "lifting", "reaching", "touching", "grasping",
    "sitting", "standing", "walking", "turning", "reaching out",
    # 人物相关剧情态（间接暗示人在场）
    "穿着", "戴着", "披着", "正在看", "注视", "凝视", "望向",
]

# scene_category → default people_policy mode（来自 image_generation_profiles.json）
CATEGORY_DEFAULT_MODE = {
    "public_commerce":         "background_groups_required",
    "civic_military":          "background_people_optional",
    "public_hall":             "background_groups_required",
    "private_interior":        "empty_required",
    "wilderness_dreamscape":   "empty_required",
    "abandoned_void":          "empty_required",
}

# scene_category 关键词（用于 _infer_people_policy / _build_scene_selector 推断）
CATEGORY_KEYWORDS = {
    "public_commerce":       ["集市", "街市", "商街", "长街", "码头", "港口", "渡口", "驿站", "客栈", "茶寮", "广场", "桥头", "商队", "马队", "驼队", "店铺", "铺面", "酒肆", "茶楼"],
    "civic_military":        ["城门", "宫门", "阙楼", "衙门", "府衙", "公堂", "军营", "营寨", "帅帐", "战场", "阵地", "前线", "行军", "巡逻", "哨卡", "校场", "演武场"],
    "public_hall":           ["宴会厅", "朝堂", "金銮殿", "酒楼", "茶馆", "食肆", "会客厅", "议事厅", "大雄宝殿", "正殿", "祖师殿"],
    "private_interior":      ["卧室", "闺房", "寝宫", "书房", "书斋", "静室", "密室", "暗室", "地窖", "偏院", "内院", "后院"],
    "wilderness_dreamscape": ["山林", "密林", "竹海", "松林", "河岸", "湖畔", "溪畔", "水湄", "山洞", "洞穴", "石窟", "荒野", "戈壁", "沙漠", "莽原", "梦境", "幻境", "虚空", "心象", "悬崖", "山巅", "绝顶", "峭壁"],
    "abandoned_void":        ["空庭院", "废园", "荒院", "空巷", "窄巷", "长巷", "里弄", "废墟", "遗迹", "残垣", "荒台", "废站台", "旧码头", "空仓库", "长廊", "游廊", "回廊", "空庙", "空祠堂"],
}


# ----------------------- dataclasses -----------------------

@dataclass
class AnalyzerStats:
    success: bool
    fallback: bool
    scene_count: int
    elapsed: float
    reason: str = ""


# ----------------------- service -----------------------

class BackgroundSceneAnalyzerService:
    """L3.01 — 章节背景场景分析器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._client = None
        self._segmenter = SceneSegmenterService()
        self._stats: List[AnalyzerStats] = []
        # 加载 image_generation_profiles.json 的 scene taxonomy
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
            logger.warning("load image_generation_profiles failed: %s", e)
            return {}

    # --------- llm client ---------

    @property
    def client(self):
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=ANALYZER_API_KEY,
                pool_env=("BG_ANALYZER_API_KEYS", "REWRITER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=ANALYZER_BASE_URL,
            )
        return self._client

    # L3.02: _call_llm
    async def _call_llm(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """带重试 + 指数退避 + JSON response_format 的 LLM 调用"""
        last_err: Optional[Exception] = None
        for attempt in range(ANALYZER_RETRIES + 1):
            attempt_user_prompt = (
                self._compact_user_prompt_for_retry(user_prompt)
                if attempt > 0 else user_prompt
            )
            attempt_start = time.monotonic()
            try:
                logger.info(
                    "analyzer LLM request attempt %d/%d model=%s timeout=%.1fs "
                    "max_tokens=%d prompt_chars=%d",
                    attempt + 1,
                    ANALYZER_RETRIES + 1,
                    ANALYZER_MODEL,
                    ANALYZER_TIMEOUT,
                    ANALYZER_MAX_TOKENS,
                    len(system_prompt) + len(attempt_user_prompt),
                )
                resp = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=resolve_request_model(ANALYZER_MODEL),
                        temperature=ANALYZER_TEMPERATURE,
                        max_tokens=ANALYZER_MAX_TOKENS,
                        timeout=ANALYZER_TIMEOUT,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": attempt_user_prompt},
                        ],
                    ),
                    timeout=ANALYZER_TIMEOUT,
                )
                content = resp.choices[0].message.content or "{}"
                return self._parse_llm_json_response(content)
            except asyncio.TimeoutError as e:
                elapsed = time.monotonic() - attempt_start
                last_err = RuntimeError(f"timeout after {elapsed:.1f}s")
                logger.warning(
                    "analyzer LLM timeout (attempt %d/%d, elapsed=%.1fs, "
                    "prompt_chars=%d, max_tokens=%d)",
                    attempt + 1,
                    ANALYZER_RETRIES + 1,
                    elapsed,
                    len(system_prompt) + len(attempt_user_prompt),
                    ANALYZER_MAX_TOKENS,
                )
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning(
                    "analyzer LLM json parse failed (attempt %d/%d): %s "
                    "content_chars=%d tail=%r",
                    attempt + 1,
                    ANALYZER_RETRIES + 1,
                    e,
                    len(content) if "content" in locals() else 0,
                    (content[-200:] if "content" in locals() else ""),
                )
            except Exception as e:
                last_err = e
                logger.warning("analyzer LLM call failed (attempt %d): %s", attempt + 1, e)
            if attempt < ANALYZER_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))  # 0.5s, 1s
        raise RuntimeError(f"analyzer LLM failed after {ANALYZER_RETRIES + 1} attempts: {last_err}")

    def _parse_llm_json_response(self, content: str) -> Dict[str, Any]:
        """解析 analyzer LLM JSON；截断时尽量回收完整 scene 对象。"""
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return {"scenes": data}
            if isinstance(data, dict):
                return data
            return {}
        except json.JSONDecodeError as original_error:
            try:
                from app.services.llm_service import parse_llm_json
                repaired = parse_llm_json(content, operation="background_scene.analyze")
                if isinstance(repaired, list):
                    return {"scenes": repaired}
                if isinstance(repaired, dict):
                    return repaired
            except Exception:
                pass

            recovered_scenes = self._recover_complete_scene_objects(content)
            if recovered_scenes:
                logger.warning(
                    "analyzer recovered %d complete scene(s) from malformed JSON",
                    len(recovered_scenes),
                )
                return {"scenes": recovered_scenes}
            raise original_error

    @staticmethod
    def _recover_complete_scene_objects(content: str) -> List[Dict[str, Any]]:
        """从被截断的 {"scenes": [...]} 响应里提取已完整闭合的对象。"""
        if not content:
            return []

        match = re.search(r'"scenes"\s*:\s*\[', content)
        if match:
            array_start = match.end() - 1
        else:
            array_start = content.find("[")
            if array_start < 0:
                return []

        objects: List[Dict[str, Any]] = []
        array_depth = 0
        object_depth = 0
        object_start: Optional[int] = None
        in_string = False
        escape = False

        for i in range(array_start, len(content)):
            ch = content[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "[" and object_depth == 0:
                array_depth += 1
            elif ch == "]" and object_depth == 0:
                array_depth -= 1
                if array_depth <= 0:
                    break
            elif ch == "{" and array_depth >= 1:
                if object_depth == 0:
                    object_start = i
                object_depth += 1
            elif ch == "}" and object_depth > 0:
                object_depth -= 1
                if object_depth == 0 and object_start is not None:
                    candidate = content[object_start:i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict):
                        objects.append(parsed)
                    object_start = None

        return objects

    @staticmethod
    def _trim_tagged_section(prompt: str, tag: str, max_chars: int) -> str:
        """保留 XML-like 标签，压缩标签体，供 timeout retry 使用。"""
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"
        start = prompt.find(open_tag)
        if start < 0:
            return prompt
        body_start = start + len(open_tag)
        end = prompt.find(close_tag, body_start)
        if end < 0:
            return prompt
        body = prompt[body_start:end].strip()
        if len(body) <= max_chars:
            return prompt
        trimmed = body[:max_chars].rstrip() + "\n...(retry truncated)..."
        return prompt[:body_start] + "\n" + trimmed + "\n" + prompt[end:]

    def _compact_user_prompt_for_retry(self, user_prompt: str) -> str:
        """第二轮重试自动缩短正文/大纲，避免重复发送同一个超时请求。"""
        prompt = self._trim_tagged_section(
            user_prompt, "ChapterOutline", ANALYZER_RETRY_OUTLINE_CHAR_LIMIT
        )
        prompt = self._trim_tagged_section(
            prompt, "ChapterText", ANALYZER_RETRY_CONTENT_CHAR_LIMIT
        )
        return prompt + (
            "\n<RetryInstruction>\n"
            "上一次请求超时或 JSON 格式无效。请只输出最必要的、可被 json.loads 解析的 JSON，最多 "
            f"{ANALYZER_MAX_SCENES} 个 scenes，字段保持简短。\n"
            "不要输出 markdown，不要输出解释，不要输出未闭合字符串。\n"
            "</RetryInstruction>\n"
        )

    # --------- validators ---------

    # L3.03: _validate_no_plot
    def _validate_no_plot(self, spec_dict: Dict[str, Any], forbidden_chars: List[str]) -> Tuple[bool, str]:
        """检查 LLM 输出是否漏入剧情/对白/角色名"""
        text = " ".join([
            str(spec_dict.get("environment_description", "")),
            str(spec_dict.get("architecture", "")),
            str(spec_dict.get("props", "")),
            str(spec_dict.get("atmosphere", "")),
        ])
        for kw in PLOT_KEYWORDS_ZH:
            if kw in text:
                return False, f"plot keyword leaked: {kw}"
        for char in forbidden_chars:
            if char and char in text:
                return False, f"forbidden character leaked: {char}"
        return True, ""

    # L3.04: _validate_split_reasons
    @staticmethod
    def _scene_fingerprint(spec_dict: Dict[str, Any]) -> str:
        """生成场景指纹（用于检测同地点重复切场景）"""
        return "|".join([
            str(spec_dict.get("scene_type", "")),
            str(spec_dict.get("time_of_day", "")),
            str(spec_dict.get("weather", "")),
            str(spec_dict.get("people_policy", {}).get("mode", "")),
        ])

    def _validate_split_reasons(self, specs: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """同 fingerprint 的连续 scene 拒绝（除非 split_reason 明确）"""
        for i in range(1, len(specs)):
            prev_fp = self._scene_fingerprint(specs[i - 1])
            curr_fp = self._scene_fingerprint(specs[i])
            if prev_fp == curr_fp:
                reason = str(specs[i].get("split_reason") or "").strip()
                if not reason:
                    return False, f"scene[{i}] duplicate fingerprint without split_reason"
        return True, ""

    # L3.05: _infer_people_policy
    def _infer_people_policy(self, spec_dict: Dict[str, Any]) -> PeoplePolicy:
        """根据 scene_category 默认 + 关键词证据推断 people_policy"""
        scene_type = spec_dict.get("scene_type", "")
        env_text = str(spec_dict.get("environment_description", "")) + str(spec_dict.get("atmosphere", ""))

        # 默认按 category
        default_mode = CATEGORY_DEFAULT_MODE.get(scene_type, "empty_required")

        # 关键词覆盖
        public_hits = sum(1 for kw in PUBLIC_ACTIVITY_SIGNALS if kw in env_text)
        empty_hits = sum(1 for kw in EMPTY_SIGNALS if kw in env_text)

        if empty_hits > public_hits:
            mode = "empty_required"
            rationale = f"empty_signal_keywords_hit={empty_hits}"
        elif public_hits > empty_hits and default_mode != "empty_required":
            mode = default_mode  # 保留 category default（可能是 groups_required）
            rationale = f"public_activity_keywords_hit={public_hits}, category_default={default_mode}"
        else:
            mode = default_mode
            rationale = f"category_default={default_mode}"

        # 已有 people_policy 覆盖（LLM 给了就用 LLM 的，但必须 enum 合法）
        existing = spec_dict.get("people_policy")
        if isinstance(existing, dict):
            try:
                return PeoplePolicy(
                    mode=existing.get("mode", mode),
                    rationale=existing.get("rationale") or rationale,
                )
            except Exception:
                pass

        return PeoplePolicy(mode=mode, rationale=rationale)

    # L3.06: _build_scene_selector deterministic slug
    @staticmethod
    def _slugify_zh(text: str, max_len: int = 30) -> str:
        """中文字符串 → ASCII slug（hash + 短前缀）"""
        if not text:
            return "x"
        # 取首 4 个非空白字符做前缀（pinyin 不可用，用 hash）
        prefix = re.sub(r"[\s\W_]+", "", text)[:4]
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        slug = f"{prefix}_{h}"
        return re.sub(r"[^a-z0-9_\-]", "", slug.lower())[:max_len] or "scene"

    def _build_scene_selector(self, spec_dict: Dict[str, Any], scene_name: str, idx: int) -> str:
        """Build a content-grounded selector for one visual moment.

        ``scene_type + time`` is not enough: two fights in the same street or
        two conversations in the same room used to collapse onto the first
        matching background. The semantic hash keeps location, state, props,
        evidence and the segment coordinate in the identity.
        """
        scene_type = str(spec_dict.get("scene_type", "x"))
        time_of_day = str(spec_dict.get("time_of_day", "unknown"))
        weather = str(spec_dict.get("weather") or "").strip() or "clear"
        mode = str(spec_dict.get("people_policy", {}).get("mode", "empty_required")) if isinstance(spec_dict.get("people_policy"), dict) else "empty_required"
        # physical_state 从 environment_description 抽简短标签
        env_desc = str(spec_dict.get("environment_description", ""))
        physical_state = "default"
        for word, label in [
            ("废墟", "ruins"), ("积水", "flooded"), ("燃烧", "burning"),
            ("积雪", "snowy"), ("荒废", "abandoned"), ("潮湿", "wet"),
            ("整洁", "tidy"), ("破败", "ruined"),
        ]:
            if word in env_desc:
                physical_state = label
                break

        semantic_payload = {
            "scene_name": scene_name,
            "scene_type": scene_type,
            "time_of_day": time_of_day,
            "weather": weather,
            "physical_state": physical_state,
            "environment_description": str(spec_dict.get("environment_description") or ""),
            "architecture": str(spec_dict.get("architecture") or ""),
            "props": str(spec_dict.get("props") or ""),
            "evidence_spans": spec_dict.get("evidence_spans") or [],
            "segment_id": spec_dict.get("segment_id"),
            "index": idx,
            "recipe_version": ANALYZER_RECIPE_VERSION,
        }
        semantic_hash = hashlib.sha256(
            json.dumps(
                semantic_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]

        # weather 也 slugify
        w_slug = self._slugify_zh(weather, max_len=12)
        return (
            f"{scene_type}__{time_of_day}__{w_slug}__{mode}__"
            f"{physical_state}__{semantic_hash}"
        )

    # --------- cache ---------

    # L3.07: _cache_key / _cache_get / _cache_set
    def _cache_key(
        self,
        chapter_index: int,
        chapter_content: str,
        outline: Dict[str, Any],
        forbidden_chars: List[str],
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        # Stage_Background_Entity_Exclusion — include the exclusion version in
        # the cache key so that bumping the constant (or changing the entity
        # list) automatically invalidates stale disk caches that still have
        # forbidden_entities=[] from before the upgrade.
        from app.services.background_story_entity_text import (
            BACKGROUND_ENTITY_EXCLUSION_VERSION,
        )

        h = hashlib.sha256()
        h.update(ANALYZER_RECIPE_VERSION.encode("utf-8"))
        h.update(b"|")
        h.update(str(chapter_index).encode("utf-8"))
        h.update(b"|")
        h.update(chapter_content.encode("utf-8"))
        h.update(b"|")
        h.update(json.dumps(outline, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        h.update(b"|")
        h.update("|".join(sorted(forbidden_chars)).encode("utf-8"))
        h.update(b"|")
        h.update(BACKGROUND_ENTITY_EXCLUSION_VERSION.encode("utf-8"))
        h.update(b"|")
        # Also fold a stable hash of the entity list in, so adding/removing
        # characters between runs invalidates the cache even when version
        # stays constant.
        if forbidden_entities:
            ents_canonical = sorted(
                f"{e.get('character_id', '')}|{e.get('canonical_name', '')}|{e.get('species', '')}"
                for e in forbidden_entities
                if isinstance(e, dict)
            )
            h.update("\n".join(ents_canonical).encode("utf-8"))
        h.update(b"|")
        h.update(str(self._config.get("_version", "0")).encode("utf-8"))
        return h.hexdigest()

    def _cache_get(self, key: str) -> Optional[List[BackgroundSceneSpec]]:
        path = CACHE_DIR / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [BackgroundSceneSpec(**d) for d in data]
        except Exception as e:
            logger.warning("cache load failed for %s: %s", key, e)
            return None

    def _cache_set(self, key: str, specs: List[BackgroundSceneSpec]) -> None:
        path = CACHE_DIR / f"{key}.json"
        try:
            path.write_text(
                json.dumps([s.model_dump(mode="json") for s in specs], ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning("cache write failed for %s: %s", key, e)

    # --------- fallback ---------

    @staticmethod
    def _split_compound_scene_name(scene_name: str) -> List[str]:
        """把 fallback 的复合场景名保守拆成多个物理地点。"""
        raw = (scene_name or "").strip()
        if not raw:
            return []
        parts = re.split(r"\s*(?:/|、|，|,|与|和)\s*", raw)
        out: List[str] = []
        seen: set[str] = set()
        for part in parts:
            part = part.strip(" 　-—")
            if not part or part in seen:
                continue
            # 避免把短语切成无意义碎片。
            if len(part) < 2:
                continue
            seen.add(part)
            out.append(part)
        return out if len(out) >= 2 else [raw]

    @staticmethod
    def _is_safe_background_visual_keyword(value: Any, forbidden_chars: List[str]) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        # 长度只能作为兜底，不能代替语义过滤。像“黑底绿字的终端报错”这类
        # 安全的环境细节会超过 8 个字符，而人物动作已经由下方黑名单过滤。
        # 仍设置上限，避免把整段剧情误当成背景要素带入 prompt。
        if len(text) > 48:
            return False
        lower = text.lower()
        if any(c and c in text for c in forbidden_chars):
            return False
        return not any(kw.lower() in lower for kw in BACKGROUND_UNSAFE_VISUAL_KEYWORDS)

    def _safe_visual_keywords(self, outline: Dict[str, Any], forbidden_chars: List[str]) -> List[str]:
        raw = outline.get("visual_keywords") or []
        if not isinstance(raw, list):
            raw = [raw]
        return [
            str(item).strip()
            for item in raw
            if self._is_safe_background_visual_keyword(item, forbidden_chars)
        ][:4]

    @staticmethod
    def _fallback_environment_description(
        location: str,
        outline: Dict[str, Any],
        safe_keywords: Optional[List[str]],
    ) -> str:
        # fallback 仍保持“纯空环境”约束，但保留已通过人物名、人体部位和
        # 动作黑名单校验的环境语义。否则分析器不可用时会同时丢掉学士帽、
        # 终端报错等决定场景辨识度的安全信息。
        parts = [
            "fallback safe background environment",
            f"地点：{location or outline.get('scene') or '未指定'}",
            "画面 100% 由建筑、景观、家具、器物、光线、天气、材质和空间氛围构成",
            "场景为纯空环境，整幅画面内无任何生物、角色、人形或生命体存在",
            "只渲染静态环境元素：建筑结构、墙面材质、地面纹理、自然光、空间层次",
            "使用宽幅环境建立镜头表现地点本身，避免任何角色动作和剧情瞬间",
        ]
        if safe_keywords:
            parts.append(f"环境视觉要素：{'、'.join(safe_keywords[:4])}")
        env_desc = " | ".join(parts)
        if len(env_desc) < 120:
            env_desc += " " + ("空旷环境细节、材质纹理、静态道具、自然光影、空间层次。" * 4)
        return env_desc[:1500]

    # L3.08: _fallback_to_segmenter
    async def _fallback_to_segmenter(
        self,
        chapter_content: str,
        outline: Dict[str, Any],
        forbidden_chars: List[str],
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> List[BackgroundSceneSpec]:
        """Analyzer 失败后的安全 fallback。

        默认不再二次调用 segmenter LLM，避免 analyzer timeout 后继续等待另一个
        LLM timeout。需要旧行为时可设置 BG_ANALYZER_USE_SEGMENTER_FALLBACK_LLM=true。
        """
        segments: List[SceneSegment] = []
        if ANALYZER_USE_SEGMENTER_FALLBACK_LLM:
            try:
                segments = await self._segmenter.segment_chapter(
                    chapter_content, outline
                )
            except Exception as e:
                logger.warning("segmenter fallback failed: %s", e)
                segments = []
        else:
            logger.info("analyzer fallback uses deterministic outline scene; segmenter LLM skipped")

        if not segments:
            outline_scene = str(outline.get("scene") or "").strip()
            if not outline_scene:
                summary = str(outline.get("summary") or "").strip()
                outline_scene = summary[:30] if summary else "unknown"
            segments = [
                SceneSegment(
                    segment_id=1,
                    location=outline_scene,
                    location_en="unknown",
                    mood=str(outline.get("emotion") or "day"),
                )
            ]

        if len(segments) == 1 and not (segments[0].environment_hint_en or "").strip():
            split_locations = self._split_compound_scene_name(segments[0].location)
            if len(split_locations) > 1:
                segments = [
                    SceneSegment(
                        segment_id=i + 1,
                        location=loc,
                        location_en="",
                        mood=segments[0].mood,
                    )
                    for i, loc in enumerate(split_locations)
                ]

        specs: List[BackgroundSceneSpec] = []
        safe_keywords = self._safe_visual_keywords(outline, forbidden_chars)
        for i, seg in enumerate(segments):
            scene_type = self._infer_scene_type(seg.location + " " + (seg.location_en or ""))
            mode = "empty_required"
            selector = self._build_scene_selector(
                {"scene_type": scene_type, "time_of_day": "unknown"},
                seg.location, i + 1
            )
            env_desc = self._fallback_environment_description(
                seg.location or "未指定",
                outline,
                safe_keywords=safe_keywords,
            )
            try:
                spec = BackgroundSceneSpec(
                    scene_id=f"fallback-{i+1}",
                    scene_name=seg.location or f"场景{i+1}",
                    scene_selector=selector,
                    scene_type=scene_type,
                    # Carry the segmenter fingerprint on the fallback path too,
                    # so the assembler's deterministic match still works when
                    # the analyzer LLM is unavailable.
                    segment_id=int(seg.segment_id) if seg.segment_id else None,
                    segment_location=str(seg.location or ""),
                    environment_description=env_desc,
                    lighting="未指定",
                    atmosphere="未指定",
                    camera_shot_type="establishing_wide",
                    people_policy=PeoplePolicy(
                        mode=mode,
                        rationale="fallback safe empty environment: analyzer/segmenter unavailable"
                    ),
                    forbidden_characters=forbidden_chars,
                    forbidden_entities=list(forbidden_entities or []),
                )
                specs.append(spec)
            except Exception as e:
                logger.warning("fallback spec build failed for seg %d: %s", i, e)
        return specs

    def _infer_scene_type(self, text: str) -> str:
        """根据文本命中推断 scene_type"""
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return cat
        return "abandoned_void"

    # --------- main entry ---------

    async def analyze_chapter(
        self,
        chapter_index: int,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        story_bible: Optional[Dict[str, Any]] = None,
        forbidden_characters: Optional[List[str]] = None,
        use_cache: bool = True,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> List[BackgroundSceneSpec]:
        """
        章节背景场景分析入口。

        Args:
            chapter_index: 章节序号
            chapter_content: 章节正文（中文）
            chapter_outline: 章节大纲 dict（含 scene/summary/characters/conflict/...）
            story_bible: 故事设定 dict（含 characters[].aliases）
            forbidden_characters: 禁用命名角色（中+英），若 None 则从 outline + story_bible 推断
            use_cache: 是否启用磁盘缓存
            forbidden_entities: Stage_Background_Entity_Exclusion — 结构化剧情角色
                实体清单（含 species/aliases/signature），用于让 LLM analyzer
                知道禁入对象覆盖人类以外的物种。若 None 则自动从 story_bible +
                outline 收集（见 :mod:`background_story_entity_collector`）。

        Returns:
            List[BackgroundSceneSpec]，至少 1 个
        """
        # 合并 forbidden_characters
        if forbidden_characters is None:
            forbidden_characters = self._collect_forbidden_characters(chapter_outline, story_bible)

        # 合并 forbidden_entities — 让 LLM analyzer 看到物种/外观特征
        if forbidden_entities is None:
            try:
                from app.services.background_story_entity_collector import (
                    collect_forbidden_story_entities,
                )

                sb_obj = story_bible
                if isinstance(sb_obj, dict):
                    sb_obj = type("SB", (), {"characters": sb_obj.get("characters")})()
                outline_obj = chapter_outline
                if isinstance(outline_obj, dict):
                    outline_obj = type("O", (), {"characters": outline_obj.get("characters")})()
                collected = collect_forbidden_story_entities(
                    story_bible=sb_obj,
                    chapter_outline=outline_obj,
                    chapter_content=chapter_content or "",
                    scene_segments=[],
                )
                forbidden_entities = [e.to_dict() for e in collected]
            except Exception as exc:  # noqa: BLE001
                logger.warning("collect_forbidden_story_entities failed: %s", exc)
                forbidden_entities = []

        # cache hit
        if use_cache:
            key = self._cache_key(chapter_index, chapter_content, chapter_outline, forbidden_characters, forbidden_entities)
            cached = self._cache_get(key)
            if cached is not None:
                logger.info("analyzer cache hit: chapter=%d", chapter_index)
                return cached

        # 早退：API key 缺失或 disabled
        if not ANALYZER_ENABLED or not api_key_available(
            ANALYZER_API_KEY,
            pool_env=("BG_ANALYZER_API_KEYS", "REWRITER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info("analyzer disabled: API key not configured, fallback to segmenter")
            return await self._fallback_to_segmenter(chapter_content, chapter_outline, forbidden_characters, forbidden_entities)

        # 正文短：直接 fallback
        if not chapter_content or len(chapter_content) < 400:
            logger.info("chapter content too short (%d chars), fallback", len(chapter_content or ""))
            return await self._fallback_to_segmenter(chapter_content, chapter_outline, forbidden_characters, forbidden_entities)

        start = time.time()
        try:
            spec_dicts = await asyncio.wait_for(
                self._call_llm_with_chapter(
                    chapter_content, chapter_outline, story_bible or {},
                    forbidden_characters, forbidden_entities,
                ),
                timeout=ANALYZER_TIMEOUT * 3,
            )
        except asyncio.TimeoutError:
            logger.warning("analyzer timed out, fallback")
            self._stats.append(AnalyzerStats(False, True, 0, time.time() - start, "timeout"))
            return await self._fallback_to_segmenter(chapter_content, chapter_outline, forbidden_characters, forbidden_entities)
        except Exception as e:
            logger.warning("analyzer failed: %s", e)
            self._stats.append(AnalyzerStats(False, True, 0, time.time() - start, str(e)))
            return await self._fallback_to_segmenter(chapter_content, chapter_outline, forbidden_characters, forbidden_entities)

        # validate + build Pydantic
        specs = self._build_specs(spec_dicts, forbidden_characters, forbidden_entities)
        if not specs:
            logger.warning("analyzer produced no specs, fallback")
            self._stats.append(AnalyzerStats(False, True, 0, time.time() - start, "empty_specs"))
            return await self._fallback_to_segmenter(chapter_content, chapter_outline, forbidden_characters, forbidden_entities)

        # Attach the shared segmenter's stable segment_id + location to every
        # spec. Legacy graph tooling independently called the same segmenter
        # singleton (in-memory cache shared per scene_segmenter_cache.md), so
        # both sides end up with the same fingerprint and the assembler can
        # bind BackGroundNode ↔ background Asset by ID rather than by
        # LLM-derived display names.
        specs = await self._annotate_specs_with_segment_fingerprint(
            specs, chapter_content, chapter_outline,
        )

        self._stats.append(AnalyzerStats(True, False, len(specs), time.time() - start))

        if use_cache:
            self._cache_set(key, specs)

        return specs

    async def enrich_segments(
        self,
        segments: Sequence[SceneSegment],
        *,
        chapter_index: int,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        story_bible: Optional[Dict[str, Any]] = None,
        forbidden_characters: Optional[List[str]] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
        use_cache: bool = True,
    ) -> List[BackgroundSceneSpec]:
        """VNGraph-aligned path: segment list is authoritative for count + ids.

        ``SceneSegmenterService`` / VNGraph BackGroundNodes decide how many
        backgrounds exist and which ``segment_id`` each maps to. This method
        only enriches each segment (environment / lighting / camera / …) via
        the analyzer LLM when available; on LLM failure it still returns
        exactly ``len(segments)`` specs so generation and assembly stay 1:1.
        """
        outline = chapter_outline or {}
        segs = list(segments or [])
        if not segs:
            outline_scene = str(outline.get("scene") or "").strip()
            if not outline_scene:
                summary = str(outline.get("summary") or "").strip()
                outline_scene = summary[:30] if summary else "unknown"
            segs = [
                SceneSegment(
                    segment_id=1,
                    location=outline_scene,
                    location_en="unknown",
                    mood=str(outline.get("emotion") or "day"),
                )
            ]

        if forbidden_characters is None:
            forbidden_characters = self._collect_forbidden_characters(outline, story_bible)
        if forbidden_entities is None:
            try:
                from app.services.background_story_entity_collector import (
                    collect_forbidden_story_entities,
                )

                sb_obj = story_bible
                if isinstance(sb_obj, dict):
                    sb_obj = type("SB", (), {"characters": sb_obj.get("characters")})()
                outline_obj = outline
                if isinstance(outline_obj, dict):
                    outline_obj = type("O", (), {"characters": outline_obj.get("characters")})()
                collected = collect_forbidden_story_entities(
                    story_bible=sb_obj,
                    chapter_outline=outline_obj,
                    chapter_content=chapter_content or "",
                    scene_segments=list(segs),
                )
                forbidden_entities = [e.to_dict() for e in collected]
            except Exception as exc:  # noqa: BLE001
                logger.warning("collect_forbidden_story_entities failed: %s", exc)
                forbidden_entities = []

        cache_key = None
        if use_cache:
            seg_fp = "|".join(
                f"{getattr(s, 'segment_id', i)}:"
                f"{getattr(s, 'scene_fingerprint', '') or getattr(s, 'location', '')}"
                for i, s in enumerate(segs, start=1)
            )
            cache_key = self._cache_key(
                chapter_index,
                chapter_content,
                {**outline, "_segment_auth_fp": seg_fp},
                forbidden_characters,
                forbidden_entities,
            )
            cached = self._cache_get(cache_key)
            if cached is not None and len(cached) == len(segs):
                logger.info(
                    "enrich_segments cache hit: chapter=%d segments=%d",
                    chapter_index, len(segs),
                )
                return cached

        analyzer_specs: List[BackgroundSceneSpec] = []
        start = time.time()
        can_call_llm = (
            ANALYZER_ENABLED
            and api_key_available(
                ANALYZER_API_KEY,
                pool_env=("BG_ANALYZER_API_KEYS", "REWRITER_API_KEYS", "OPENAI_API_KEYS"),
            )
            and chapter_content
            and len(chapter_content) >= 400
        )
        if can_call_llm:
            try:
                spec_dicts = await asyncio.wait_for(
                    self._call_llm_with_chapter(
                        chapter_content, outline, story_bible or {},
                        forbidden_characters, forbidden_entities,
                    ),
                    timeout=ANALYZER_TIMEOUT * 3,
                )
                analyzer_specs = self._build_specs(
                    spec_dicts, forbidden_characters, forbidden_entities,
                )
                self._stats.append(
                    AnalyzerStats(True, False, len(analyzer_specs), time.time() - start)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "enrich_segments LLM failed (will use segment fallbacks): %s", exc,
                )
                self._stats.append(
                    AnalyzerStats(False, True, 0, time.time() - start, str(exc))
                )
                analyzer_specs = []
        else:
            logger.info(
                "enrich_segments skipping analyzer LLM "
                "(disabled/short content/no key); segment fallbacks only"
            )

        specs = self._align_specs_to_segments(
            segs,
            analyzer_specs,
            outline=outline,
            forbidden_characters=forbidden_characters,
            forbidden_entities=forbidden_entities,
        )
        if use_cache and cache_key is not None:
            self._cache_set(cache_key, specs)
        return specs

    def _align_specs_to_segments(
        self,
        segments: Sequence[SceneSegment],
        analyzer_specs: Sequence[BackgroundSceneSpec],
        *,
        outline: Dict[str, Any],
        forbidden_characters: List[str],
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> List[BackgroundSceneSpec]:
        """Force 1:1 specs for segments; copy enrich fields from analyzer when matched."""
        pool = list(analyzer_specs or [])
        used: set = set()
        out: List[BackgroundSceneSpec] = []
        for index, seg in enumerate(segments):
            matched: Optional[BackgroundSceneSpec] = None
            seg_loc = (getattr(seg, "location", None) or "").strip()
            for i, spec in enumerate(pool):
                if i in used:
                    continue
                name = (getattr(spec, "scene_name", None) or "").strip()
                loc = (getattr(spec, "segment_location", None) or "").strip()
                if seg_loc and (name == seg_loc or loc == seg_loc):
                    matched = spec
                    used.add(i)
                    break
            if matched is None:
                for i, spec in enumerate(pool):
                    if i not in used:
                        matched = spec
                        used.add(i)
                        break
            if matched is None:
                matched = self._spec_from_segment_fallback(
                    seg,
                    index=index,
                    outline=outline,
                    forbidden_characters=forbidden_characters,
                    forbidden_entities=forbidden_entities,
                )
            else:
                matched = matched.model_copy(deep=True)
            out.append(self._stamp_segment_identity(matched, seg, index=index))
        return out

    def _stamp_segment_identity(
        self,
        spec: BackgroundSceneSpec,
        seg: SceneSegment,
        *,
        index: int = 0,
    ) -> BackgroundSceneSpec:
        """Overwrite identity/fingerprint fields from the authoritative segment."""
        sid = int(getattr(seg, "segment_id", None) or (index + 1))
        loc = str(getattr(seg, "location", None) or spec.scene_name or f"场景{sid}")
        spec.segment_id = sid
        spec.segment_location = loc
        spec.segment_start_marker = str(getattr(seg, "start_marker", None) or "") or None
        spec.segment_end_marker = str(getattr(seg, "end_marker", None) or "") or None
        fp = str(getattr(seg, "scene_fingerprint", None) or "").strip()
        if fp:
            spec.scene_fingerprint = fp
        ev = str(getattr(seg, "event_signature", None) or "").strip()
        if ev:
            spec.event_signature = ev
        # Prefer segment location as the human-readable name so VNGraph
        # DisplayName / SceneLocation stay consistent with BackGroundNode.
        if loc:
            spec.scene_name = loc[:120]
        excerpt_parts = [
            str(getattr(seg, "start_marker", "") or "").strip(),
            str(getattr(seg, "summary", "") or "").strip(),
        ]
        excerpt = " ".join(p for p in excerpt_parts if p)[:600]
        if excerpt and not (spec.source_excerpt or "").strip():
            spec.source_excerpt = excerpt
        # Keep selector unique per segment_id even if locations collide.
        scene_type = getattr(spec, "scene_type", None) or self._infer_scene_type(
            loc + " " + (getattr(seg, "location_en", "") or "")
        )
        spec.scene_type = scene_type
        spec.scene_selector = self._build_scene_selector(
            {
                "scene_type": scene_type,
                "time_of_day": getattr(spec, "time_of_day", None)
                or getattr(seg, "time_of_day", None)
                or "unknown",
            },
            loc,
            sid,
        )
        if not (spec.environment_description or "").strip():
            hint = str(getattr(seg, "environment_hint_en", "") or "").strip()
            if hint:
                # environment_description is Chinese-oriented; keep a usable
                # bilingual-ish placeholder so Pydantic min_length passes.
                padded = (
                    f"{loc}。{hint} "
                    f"空无一人的环境场景，强调建筑、道具、光线与氛围，禁止出现任何人物。"
                )
                spec.environment_description = padded[:1500]
        return spec

    def _spec_from_segment_fallback(
        self,
        seg: SceneSegment,
        *,
        index: int,
        outline: Dict[str, Any],
        forbidden_characters: List[str],
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> BackgroundSceneSpec:
        """Build a minimal empty-environment spec from one authoritative segment."""
        loc = str(getattr(seg, "location", None) or f"场景{index + 1}")
        scene_type = self._infer_scene_type(loc + " " + (getattr(seg, "location_en", "") or ""))
        selector = self._build_scene_selector(
            {
                "scene_type": scene_type,
                "time_of_day": getattr(seg, "time_of_day", None) or "unknown",
            },
            loc,
            int(getattr(seg, "segment_id", None) or (index + 1)),
        )
        safe_keywords = self._safe_visual_keywords(outline, forbidden_characters)
        hint = str(getattr(seg, "environment_hint_en", "") or "").strip()
        if hint:
            env_desc = (
                f"{loc}。{hint} "
                f"空无一人的环境场景，强调建筑、道具、光线与氛围，禁止出现任何人物。"
            )[:1500]
        else:
            env_desc = self._fallback_environment_description(
                loc, outline, safe_keywords=safe_keywords,
            )
        tod = str(getattr(seg, "time_of_day", None) or "unknown")
        if tod not in {
            "dawn", "morning", "noon", "afternoon", "dusk", "evening",
            "night", "late_night", "unknown",
        }:
            tod = "unknown"
        return BackgroundSceneSpec(
            scene_id=f"seg-{getattr(seg, 'segment_id', index + 1)}",
            scene_name=loc[:120],
            scene_selector=selector,
            scene_type=scene_type,
            segment_id=int(getattr(seg, "segment_id", None) or (index + 1)),
            segment_location=loc,
            segment_start_marker=str(getattr(seg, "start_marker", "") or "") or None,
            segment_end_marker=str(getattr(seg, "end_marker", "") or "") or None,
            scene_fingerprint=str(getattr(seg, "scene_fingerprint", "") or "") or None,
            event_signature=str(getattr(seg, "event_signature", "") or "") or None,
            environment_description=env_desc,
            lighting="未指定",
            atmosphere=str(getattr(seg, "mood", None) or "未指定")[:300] or "未指定",
            time_of_day=tod,  # type: ignore[arg-type]
            camera_shot_type="establishing_wide",
            people_policy=PeoplePolicy(
                mode="empty_required",
                rationale="segment-authoritative fallback; analyzer enrich unavailable",
            ),
            forbidden_characters=list(forbidden_characters or []),
            forbidden_entities=list(forbidden_entities or []),
        )

    async def _annotate_specs_with_segment_fingerprint(
        self,
        specs: List[BackgroundSceneSpec],
        chapter_content: str,
        chapter_outline: Dict[str, Any],
    ) -> List[BackgroundSceneSpec]:
        """Cross-reference analyzer specs with the shared scene segmenter.

        Legacy helper for ``analyze_chapter``. Prefer ``enrich_segments`` when
        VNGraph segment count must be authoritative.

        Matching policy:
        1. First pass — try location exact match between spec.scene_name and
           segment.location.
        2. Second pass — remaining specs take segment IDs in segmenter order.
        3. **Collision handling** — when segmenter produces fewer segments
           than analyzer specs (analyzer is finer-grained), the leftover specs
           share a segment_id which crashes the assembler's fingerprint match.
           In that case, leftover specs get a synthetic unique id derived from
           the max segment_id + offset.  This synthetic id is also written
           to ``Asset.generation_params.segment_id`` by the downstream
           asset builder, so vn_graph (which reads from assets) and the
           assembler see the same unique fingerprint.
        """
        if not specs:
            return specs
        try:
            from app.services.scene_segmenter_service import scene_segmenter_service
            segments = await scene_segmenter_service.segment_chapter(
                chapter_content, chapter_outline,
            )
        except Exception as exc:
            logger.warning(
                "analyzer segment-fingerprint annotation skipped (segmenter unavailable): %s", exc,
            )
            return specs
        if not segments:
            # segmenter 没出来：用 spec 自身 index 作稳定 segment_id（1..N）
            for index, spec in enumerate(specs, start=1):
                spec.segment_id = index
                spec.segment_location = str(spec.scene_name or "")
            return specs

        # Pass 1: location 精确匹配
        loc_to_sid: Dict[str, int] = {}
        for s in segments:
            sid = getattr(s, "segment_id", None)
            loc = (getattr(s, "location", None) or "").strip()
            if sid is not None and loc:
                loc_to_sid[loc] = int(sid)

        assigned = [None] * len(specs)
        used: set = set()
        for i, spec in enumerate(specs):
            spec_loc = (getattr(spec, "scene_name", "") or "").strip()
            sid = loc_to_sid.get(spec_loc)
            if sid is not None and sid not in used:
                assigned[i] = sid
                used.add(sid)

        # Pass 2: 剩余按 segmenter 顺序兜底（跳过已用 id）
        seg_iter = iter(segments)
        for i, spec in enumerate(specs):
            if assigned[i] is not None:
                continue
            for s in seg_iter:
                sid = getattr(s, "segment_id", None)
                try:
                    sid = int(sid) if sid is not None else None
                except (TypeError, ValueError):
                    sid = None
                if sid is None or sid in used:
                    continue
                assigned[i] = sid
                used.add(sid)
                break

        # Pass 3: 还有剩余（analyzer 切得比 segmenter 细）—— 用 max(segments)+offset 合成唯一 id
        if any(a is None for a in assigned):
            base = max(
                (int(getattr(s, "segment_id", 0) or 0) for s in segments),
                default=0,
            ) + 1
            # 但合成 id 必须不和 used 撞
            synthetic = max(base, max(used, default=0) + 1)
            for i, spec in enumerate(specs):
                if assigned[i] is None:
                    assigned[i] = synthetic
                    used.add(synthetic)
                    synthetic += 1

        for i, spec in enumerate(specs):
            try:
                spec.segment_id = int(assigned[i])
            except (TypeError, ValueError):
                continue
            # location 优先用 spec 自身的（更准），fallback 到 segmenter
            if not getattr(spec, "segment_location", None):
                spec.segment_location = str(spec.scene_name or "")
        return specs

    # --------- LLM assembly ---------

    async def _call_llm_with_chapter(
        self,
        chapter_content: str,
        outline: Dict[str, Any],
        story_bible: Dict[str, Any],
        forbidden_chars: List[str],
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """构造 prompt + 调用 LLM + 解析为 spec dict 列表"""
        cfg = self._config.get("rewriter", {}).get("background_scene_extractor", {})
        sys_prompt_obj = cfg.get("system_prompt", {})
        system_prompt = self._render_system_prompt(sys_prompt_obj)
        user_prompt = self._render_user_prompt(
            chapter_content, outline, story_bible, forbidden_chars, forbidden_entities,
        )
        data = await self._call_llm(system_prompt, user_prompt)
        # data 可能是 {"scenes": [...]} 或直接 [...]
        if isinstance(data, dict):
            scenes = data.get("scenes") or data.get("scene_specs") or []
        else:
            scenes = data
        if not isinstance(scenes, list):
            return []
        if len(scenes) > ANALYZER_MAX_SCENES:
            logger.info(
                "analyzer returned %d scenes, truncating to %d",
                len(scenes),
                ANALYZER_MAX_SCENES,
            )
            scenes = scenes[:ANALYZER_MAX_SCENES]
        return scenes

    def _render_system_prompt(self, sys_prompt_obj: Dict[str, Any]) -> str:
        """把 5 段分区的 system_prompt dict 渲染成完整字符串"""
        # This policy is code-owned rather than config-owned. Deployments may
        # still carry an older image_generation_profiles.json which described
        # a scene as one unique physical location. Keeping the invariant here
        # prevents that stale config from collapsing a whole chapter back to
        # a handful of reusable backgrounds.
        parts = [
            """# 通用视觉时刻规则
你分析的是任意题材小说，不得依赖特定作品、时代或固定人物名单。
scene 表示一个可被画出来的视觉时刻，而不是去重后的地点名称。
地点返回、昼夜/天气/光线改变、空间物理状态改变、关键物件出现、
可见事件推进、命名人物首次进入画面，都可以产生新的 scene。
同一地点的不同视觉时刻必须保留各自原文证据和不同的环境/镜头重点。
纯对白轮换且画面没有可见变化时不要切 scene。"""
        ]
        if sys_prompt_obj.get("identity"):
            parts.append(f"# 身份\n{sys_prompt_obj['identity']}")
        if sys_prompt_obj.get("instructions"):
            parts.append(f"# 任务\n{sys_prompt_obj['instructions']}")
        rules = sys_prompt_obj.get("rules") or []
        if rules:
            rule_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))
            parts.append(f"# 硬性规则\n{rule_text}")
        if sys_prompt_obj.get("context"):
            parts.append(f"# 背景\n{sys_prompt_obj['context']}")
        examples = sys_prompt_obj.get("examples") or []
        if examples:
            ex_text = "\n\n".join(
                f"## 示例 {i+1}\n```json\n{json.dumps(ex, ensure_ascii=False, indent=2)}\n```"
                for i, ex in enumerate(examples)
            )
            parts.append(f"# 示例\n{ex_text}")
        return "\n\n".join(parts)

    def _render_user_prompt(
        self,
        chapter_content: str,
        outline: Dict[str, Any],
        story_bible: Dict[str, Any],
        forbidden_chars: List[str],
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        genre = self._infer_genre(story_bible)
        chars_block = "\n".join(f"- {c}" for c in forbidden_chars) or "- (none)"
        outline_json_full = json.dumps(outline, ensure_ascii=False, indent=2)
        outline_json = outline_json_full[:ANALYZER_OUTLINE_CHAR_LIMIT]
        if len(outline_json_full) > ANALYZER_OUTLINE_CHAR_LIMIT:
            outline_json += "\n...(outline truncated)..."
        sb_brief = (
            f"worldview={story_bible.get('worldview','')[:200]}, "
            f"main_conflict={story_bible.get('main_conflict','')[:200]}"
        )
        content_trimmed = chapter_content[:ANALYZER_CONTENT_CHAR_LIMIT]
        if len(chapter_content) > ANALYZER_CONTENT_CHAR_LIMIT:
            content_trimmed += "\n...(chapter text truncated for analyzer)..."

        # Stage_Background_Entity_Exclusion — 把"剧情角色实体"清单（含物种/
        # 别名/外观特征）注入 analyzer，让它知道禁入对象不只有人类。
        # Analyzer 只能从我们提供的清单中选择/回填，禁止编造新角色或新物种。
        if forbidden_entities:
            entity_lines = []
            for e in forbidden_entities[:12]:
                names = [e.get("canonical_name")] + list(e.get("aliases") or [])
                names = [n for n in names if n]
                species = e.get("species") or "human"
                sig = "; ".join(e.get("appearance_signature") or [])
                entity_lines.append(
                    f"- {', '.join(names)}（物种: {species}; 外观特征: {sig}）"
                )
            entities_block = (
                "<ForbiddenStoryEntities>\n"
                "以下剧情角色实体永远禁止出现在背景图里。无论物种是否为人类，\n"
                "都不得在环境描述、道具、画面焦点中体现这些角色，也不得通过剪影/\n"
                "倒影/全息/海报/雕像/复制体间接表现。场景中可以出现普通的无人格\n"
                "机械设备或工业设施，只要它们不复制以上角色的身份外观特征。\n"
                + "\n".join(entity_lines)
                + "\n</ForbiddenStoryEntities>"
            )
            entity_rule = (
                "- forbidden_entity_ids: 把上面 ForbiddenStoryEntities 列出的\n"
                "  character_id 原样回填到当前 scene 中（只能从给定清单选，不得编造）。\n"
                "- forbidden_entity_names: 同上，回填 canonical_name。\n"
                "- generic_environment_entities_allowed: str|null。如果该场景需要出现\n"
                "  普通无人格的机械设备/工业装置/无人载具/无关动物，简述允许的类别\n"
                "  （例如 \"industrial robotic arms; conveyor belt\"）；否则填 null。"
            )
        else:
            entities_block = (
                "<ForbiddenStoryEntities>\n"
                "本次未提供结构化实体清单，请只用 ForbiddenCharacters 中的命名角色\n"
                "作为禁入对象，并默认遵守「背景图禁止任何剧情角色实体」的总体规则。\n"
                "</ForbiddenStoryEntities>"
            )
            entity_rule = (
                "- forbidden_entity_ids: []（无结构化实体时填空数组）\n"
                "- forbidden_entity_names: []\n"
                "- generic_environment_entities_allowed: str|null"
            )

        return f"""<ProjectStyle>
题材: {genre}
</ProjectStyle>

<StoryBibleBrief>
{sb_brief}
</StoryBibleBrief>

<ForbiddenCharacters>
以下角色是命名角色，永远不要让他们出现在背景图的环境描述里：
{chars_block}
</ForbiddenCharacters>

{entities_block}

<ChapterOutline>
{outline_json}
</ChapterOutline>

<ChapterText>
{content_trimmed}
</ChapterText>

<OutputSchemaHint>
返回 JSON: {{"scenes": [scene_spec, ...]}}
硬限制：
- 最多输出 {ANALYZER_MAX_SCENES} 个 scenes。
- scene 代表“视觉时刻”，不是去重后的地点列表。地点返回、昼夜/天气变化、
  关键物件变化、明显破坏状态、可见事件或命名人物首次入场，都可以建立新 scene。
- 同一地点的不同视觉时刻必须分别输出，并在 evidence_spans 中给出各自的原文证据；
  不得因为 scene_name 相同而合并。
- 不要按每句对白切分；相邻 scene 必须能画出明显不同的环境状态、物件或镜头重点。
- 每个字段保持短小；不要复述剧情，不要解释切分算法。
每个 scene_spec 必须包含字段：
- scene_id (str, 短)
- scene_name (str, 中文)
- scene_type (one of: public_commerce, civic_military, public_hall, private_interior, wilderness_dreamscape, abandoned_void)
- split_reason (str|null)
- evidence_spans (list[str]，最多 3 条，每条短句)
- environment_description (str, 中文，120-220 字，只写环境不写剧情)
- architecture (str|null)
- props (str|null)
- lighting (str)
- weather (str|null)
- time_of_day (one of: dawn, morning, noon, afternoon, dusk, evening, night, late_night, unknown)
- atmosphere (str)
- camera_shot_type (one of: establishing_wide, environment_medium, high_angle_distant, interior_wide, telephoto_compressed, low_angle_perspective)
- composition_constraints (str|null)
- people_policy: {{"mode": one of [empty_required, background_people_optional, background_groups_required], "rationale": str}}
- forbidden_characters: list[str] (只把上面列出的命名角色原样回填)
- forbidden_entity_ids: list[str]
- forbidden_entity_names: list[str]
- forbidden_entity_signatures: list[str]（外观特征关键词，只能从上面 ForbiddenStoryEntities 复制）
- generic_environment_entities_allowed: str|null
{entity_rule}
</OutputSchemaHint>

只输出严格 JSON，不要 markdown 围栏，不要解释。
"""

    def _infer_genre(self, story_bible: Dict[str, Any]) -> str:
        """从 story_bible / 项目配置推断 genre（5 桶）"""
        text = (str(story_bible.get("worldview", "")) + " " +
                str(story_bible.get("main_conflict", ""))).lower()
        if any(w in text for w in ["科幻", "未来", "赛博", "sci-fi", "cyber"]):
            return "scifi"
        if any(w in text for w in ["奇幻", "魔法", "神", "魔", "fantasy"]):
            return "fantasy"
        if any(w in text for w in ["现代", "都市", "校园", "modern"]):
            return "modern"
        if any(w in text for w in ["二次元", "动漫", "anime"]):
            return "anime"
        return "historical"  # 默认

    # --------- build specs ---------

    def _build_specs(
        self,
        spec_dicts: List[Dict[str, Any]],
        forbidden_chars: List[str],
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> List[BackgroundSceneSpec]:
        """把 LLM 返回的 dict 列表规范化为 BackgroundSceneSpec 列表"""
        # validate split reasons
        ok, msg = self._validate_split_reasons(spec_dicts)
        if not ok:
            logger.warning("split reason validation failed: %s", msg)

        # Stage_Background_Entity_Exclusion — authoritative entity list comes
        # from the caller (collector), not from the LLM. The LLM may add
        # per-scene subset picks via forbidden_entity_ids, but the full
        # list is always the caller-provided one so a hallucinated entity
        # can never sneak into the prompt.
        authoritative_entities = list(forbidden_entities or [])
        authoritative_by_id = {
            e.get("character_id"): e for e in authoritative_entities
            if isinstance(e, dict) and e.get("character_id")
        }

        specs: List[BackgroundSceneSpec] = []
        for i, d in enumerate(spec_dicts):
            # validate no plot
            ok, msg = self._validate_no_plot(d, forbidden_chars)
            if not ok:
                logger.warning("scene[%d] plot leak: %s — will patch env_description", i, msg)
                # 修补：截掉 plot 段（简单处理：换为短环境描述）
                d = self._patch_plot_leak(d)

            d = self._sanitize_positive_subject_leaks(
                d,
                forbidden_chars,
                authoritative_entities,
            )

            # infer + fill missing fields
            scene_type = d.get("scene_type") or self._infer_scene_type(
                str(d.get("scene_name", "")) + str(d.get("environment_description", ""))
            )
            d["scene_type"] = scene_type
            d.setdefault("scene_id", f"s{i+1}")
            d.setdefault("time_of_day", "unknown")

            # people_policy（先 infer，再让 LLM 的覆盖）
            if "people_policy" not in d or not isinstance(d.get("people_policy"), dict):
                d["people_policy"] = self._infer_people_policy(d).model_dump()
            d["people_policy"]["mode"] = "empty_required"
            rationale = str(d["people_policy"].get("rationale") or "").strip()
            if "forced empty_required" not in rationale:
                d["people_policy"]["rationale"] = (
                    rationale + "; forced empty_required by global background policy"
                    if rationale else "forced empty_required by global background policy"
                )

            # deterministic scene_selector（覆盖 LLM 自由发挥）
            scene_name = str(d.get("scene_name") or f"scene{i+1}")
            d["scene_selector"] = self._build_scene_selector(d, scene_name, i + 1)

            # forbidden_characters 回填
            d["forbidden_characters"] = forbidden_chars

            # Stage_Background_Entity_Exclusion — 永远以调用方提供的权威清单
            # 为准。LLM 回填的 forbidden_entity_ids 只用于本地过滤，不影响
            # 最终写入 spec 的 entities 集合。
            d["forbidden_entities"] = authoritative_entities
            llm_pick_ids = d.get("forbidden_entity_ids") or []
            if isinstance(llm_pick_ids, list):
                # Sanity check: if LLM picked an id we don't know, log & ignore.
                unknown = [
                    eid for eid in llm_pick_ids
                    if eid and eid not in authoritative_by_id
                ]
                if unknown:
                    logger.warning(
                        "scene[%d] LLM picked unknown entity ids %s — ignored",
                        i, unknown,
                    )

            # style_tags 留空（由 classifier 补全）
            d["style_tags"] = []

            try:
                spec = BackgroundSceneSpec(**d)
                specs.append(spec)
            except Exception as e:
                logger.warning("scene[%d] schema build failed: %s (data=%s)", i, e, str(d)[:300])
        return specs

    @staticmethod
    def _sanitize_positive_subject_leaks(
        d: Dict[str, Any],
        forbidden_chars: List[str],
        forbidden_entities: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Remove cast/remains clauses from every positive scene-spec field."""
        cleaned = dict(d)
        names = forbidden_story_names(forbidden_chars, forbidden_entities)
        for field_name in (
            "scene_name",
            "environment_description",
            "architecture",
            "props",
            "lighting",
            "weather",
            "atmosphere",
            "composition_constraints",
        ):
            original = cleaned.get(field_name)
            if original is None:
                continue
            value = sanitize_positive_background_text(
                str(original),
                forbidden_names=names,
            )
            cleaned[field_name] = value or None

        policy = cleaned.get("people_policy")
        if isinstance(policy, dict):
            cleaned_policy = dict(policy)
            rationale = sanitize_positive_background_text(
                str(cleaned_policy.get("rationale") or ""),
                forbidden_names=names,
            )
            cleaned_policy["rationale"] = rationale or "纯环境背景"
            cleaned["people_policy"] = cleaned_policy

        if not cleaned.get("scene_name"):
            cleaned["scene_name"] = "空环境场景"
        if not cleaned.get("lighting"):
            cleaned["lighting"] = "柔和的环境漫射光"
        if not cleaned.get("atmosphere"):
            cleaned["atmosphere"] = "安静空旷"
        env = str(cleaned.get("environment_description") or "").strip()
        if len(env) < 40:
            fallback = (
                "空旷场地保留原有地貌、建筑材质与静态陈设，环境光沿空间层次展开，"
                "画面仅表现地点本身及其天气氛围。"
            )
            cleaned["environment_description"] = f"{env}，{fallback}" if env else fallback
        return cleaned

    def _patch_plot_leak(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """简单 plot-leak 修补：把 environment_description 中含剧情词的句子剔除"""
        env = str(d.get("environment_description", ""))
        # 按句号/逗号切片，剔除含剧情词的片段
        chunks = re.split(r"([。；！？\n])", env)
        cleaned: List[str] = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            if any(kw in chunk for kw in PLOT_KEYWORDS_ZH):
                continue
            cleaned.append(chunk)
        new_env = "".join(cleaned).strip()
        # 如果太短，用占位填充
        if len(new_env) < 130:
            new_env = (new_env + " 环境静谧，光线柔和，空气潮湿，时间在此处凝固。" * 3)[:1500]
        d["environment_description"] = new_env
        return d

    # --------- forbidden_characters 收集 ---------

    @staticmethod
    def _collect_forbidden_characters(
        outline: Dict[str, Any],
        story_bible: Optional[Dict[str, Any]],
    ) -> List[str]:
        """合并 outline.characters + story_bible.characters (含 aliases) + 正文命中"""
        chars: List[str] = []
        seen: set = set()

        def add(name: str) -> None:
            name = (name or "").strip()
            if not name or name.lower() in {"none", "null", "未知"}:
                return
            if name in seen:
                return
            seen.add(name)
            chars.append(name)

        for c in outline.get("characters") or []:
            if isinstance(c, str):
                add(c)
            elif isinstance(c, dict):
                add(c.get("name") or c.get("name_cn"))
                add(c.get("english_name"))
                add(c.get("alias"))

        if story_bible:
            sb_chars = story_bible.get("characters") or []
            for c in sb_chars:
                if isinstance(c, dict):
                    add(c.get("name") or c.get("name_cn"))
                    add(c.get("english_name"))
                    for alias in c.get("aliases") or c.get("nicknames") or []:
                        add(alias)

        return chars

    # --------- stats ---------

    def stats(self) -> List[AnalyzerStats]:
        return list(self._stats)

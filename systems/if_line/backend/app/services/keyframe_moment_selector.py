"""
Keyframe Moment Selector (P1.2)

输入：章节正文 + 章节大纲 + max_keyframes
输出：``List[KeyframeMoment]``，告诉 ``generate_chapter_keyframes``
      「这一章里哪 2-3 个时刻最有画面感、最该被定格成关键帧」。

职责：
- LLM 从正文挑「最有画面感 / 最该被定格」的 2-3 个时刻。
- 每个时刻输出 ``moment_summary / target_characters / visual_focus /
  source_excerpt / rationale``，供下游 ``prompt_builder_service`` 拼 prompt。
- 失败 / 关闭 / 缺输入时 fallback 到 ``prompt_builder_service`` 的旧逻辑
  （单关键帧 = 章节标题/场景），保证链路不死。

调用形态参考 ``background_scene_analyzer_service.py`` 的 ``_call_llm``：
  - ``response_format={"type": "json_object"}`` 强制 JSON 输出
  - ``asyncio.wait_for`` 外层超时兜底
  - 指数退避重试 (``0.5 * 2**attempt``)
  - sha256 缓存 → ``backend/.cache/keyframe_moment/<hash>.json``

公开入口：``await KeyframeMomentSelector().select(chapter_content, chapter_outline, max_keyframes)``
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.text_llm_config import DEFAULT_TEXT_LLM_MODEL, resolve_request_model, text_llm_api_key, text_llm_base_url, text_llm_model
from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available

logger = logging.getLogger("keyframe_moment_selector")

# ----------------------- env（与 portrait_demand_analyzer / background_scene_analyzer 一致的 fallback 链）-----------------------

KEYFRAME_MOMENT_ENABLED = os.getenv("KEYFRAME_MOMENT_ENABLED", "true").lower() == "true"
KEYFRAME_MOMENT_MODEL = text_llm_model("KEYFRAME_MOMENT_MODEL", "BG_ANALYZER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
KEYFRAME_MOMENT_API_KEY = text_llm_api_key("KEYFRAME_MOMENT_API_KEY", "BG_ANALYZER_API_KEY", "REWRITER_API_KEY")
KEYFRAME_MOMENT_BASE_URL = text_llm_base_url("KEYFRAME_MOMENT_BASE_URL", "BG_ANALYZER_BASE_URL", "REWRITER_BASE_URL")
KEYFRAME_MOMENT_TIMEOUT = float(os.getenv("KEYFRAME_MOMENT_TIMEOUT", "90.0"))
KEYFRAME_MOMENT_TEMPERATURE = float(os.getenv("KEYFRAME_MOMENT_TEMPERATURE", "0.3"))
KEYFRAME_MOMENT_MAX_TOKENS = int(os.getenv("KEYFRAME_MOMENT_MAX_TOKENS", "4000"))
KEYFRAME_MOMENT_RETRIES = int(os.getenv("KEYFRAME_MOMENT_RETRIES", "1"))

# 一次 LLM 调用，章节正文超过此字符数则截断（防爆 token）。
KEYFRAME_MOMENT_CONTENT_BUDGET = int(os.getenv("KEYFRAME_MOMENT_CONTENT_BUDGET", "6000"))

# 蓝图回退开关：``KEYFRAME_AUTO_SELECT=0`` 一键回退到旧用户选项模式。
if os.getenv("KEYFRAME_AUTO_SELECT", "1") == "0":
    KEYFRAME_MOMENT_ENABLED = False

CACHE_DIR = Path(os.getenv(
    "KEYFRAME_MOMENT_CACHE_DIR",
    str(Path(__file__).parent.parent.parent / ".cache" / "keyframe_moment"),
))

# 单时刻最多挂几个角色名（防 LLM 失控把所有路人都塞进来）。
MAX_CHARACTERS_PER_MOMENT = int(os.getenv("KEYFRAME_MOMENT_MAX_CHARACTERS", "4"))
KEYFRAME_MOMENT_RECIPE_VERSION = "keyframe-moment-v2-age-state"


# ----------------------- dataclass -----------------------

@dataclass
class KeyframeMoment:
    """单条关键时刻。

    ``target_characters`` 是这一时刻画面里出现的角色名列表（与 Story Bible
    ``characters[].name`` 对齐），下游 ``prompt_builder_service`` 会拿这个去
    Story Bible 查 appearance。

    E05 — 服装/动作/情绪状态字段：所有字段对每个角色都是 list[str]，下标与
    ``target_characters`` 对齐。空 list 或 None 表示该字段未指定，下游允许
    用空字符串填充。
    """
    moment_summary: str
    target_characters: List[str]
    visual_focus: str
    source_excerpt: str
    rationale: str
    # E05 clothing state — index aligns with target_characters
    character_emotions: Optional[List[str]] = None
    character_outfits: Optional[List[str]] = None
    character_poses: Optional[List[str]] = None
    character_actions: Optional[List[str]] = None
    character_injury_states: Optional[List[str]] = None
    character_held_items: Optional[List[str]] = None
    character_age_groups: Optional[List[str]] = None

    def fingerprint(self) -> Tuple[str, Tuple[str, ...]]:
        """dedup key: (moment_summary, sorted(target_characters))。"""
        return (self.moment_summary, tuple(sorted(self.target_characters)))

    def get_emotion(self, char_name: str) -> str:
        return _pick_state_for(self.character_emotions, self.target_characters, char_name)

    def get_outfit(self, char_name: str) -> str:
        return _pick_state_for(self.character_outfits, self.target_characters, char_name)

    def get_pose(self, char_name: str) -> str:
        return _pick_state_for(self.character_poses, self.target_characters, char_name)

    def get_action(self, char_name: str) -> str:
        return _pick_state_for(self.character_actions, self.target_characters, char_name)

    def get_injury_state(self, char_name: str) -> str:
        return _pick_state_for(self.character_injury_states, self.target_characters, char_name)

    def get_held_item(self, char_name: str) -> str:
        return _pick_state_for(self.character_held_items, self.target_characters, char_name)

    def get_age_group(self, char_name: str) -> str:
        return _pick_state_for(self.character_age_groups, self.target_characters, char_name)


def _pick_state_for(
    states: Optional[List[str]],
    names: List[str],
    char_name: str,
) -> str:
    """Return the state entry for ``char_name`` or empty string."""
    if not states or not names:
        return ""
    try:
        idx = names.index(char_name)
    except ValueError:
        return ""
    if idx < len(states) and states[idx]:
        return str(states[idx]).strip()
    return ""


# ----------------------- system prompt -----------------------

SYSTEM_PROMPT = """你是视觉小说的关键帧导演。给你一章中文小说正文 + 该章大纲，你的工作是
挑出「最有画面感、最该被定格成关键帧插画」的 2-3 个时刻。

## 输出 JSON 结构
{
  "moments": [
    {
      "moment_summary": "这一刻发生了什么（中文，<=40 字，动词开头，例'林夜持剑指向群臣'）",
      "target_characters": ["角色名1", "角色名2"],
      "visual_focus": "画面焦点（中文，<=50 字，例'前景剑尖反光，中景林夜侧身，远景群臣让出一条路'）",
      "source_excerpt": "原文里这一刻的句子片段（中文，<=80 字，必须原文截取，不能改写）",
      "rationale": "为什么这一刻该被定格（中文，<=60 字，说清画面感/情绪张力/视觉冲击）",
      "character_emotions": ["角色名1 此刻的情绪（中文，<=10 字，例'愤怒/隐忍'）", "角色名2 的情绪"],
      "character_outfits": ["角色名1 此刻穿的衣物（中文，<=20 字，例'玄色长袍，袖口银线'）", "角色名2 的衣物"],
      "character_poses": ["角色名1 此刻的姿势（中文，<=15 字，例'侧身回望，右手按剑'）", "角色名2 的姿势"],
      "character_actions": ["角色名1 此刻的动作（中文，<=15 字，例'挥剑斩落'）", "角色名2 的动作"],
      "character_injury_states": ["角色名1 此刻是否受伤/血迹（中文，<=10 字，没有就写'无'）", "角色名2 的伤情"],
      "character_held_items": ["角色名1 此刻手里拿的物品（中文，<=10 字，没有就写'空手'）", "角色名2 的物品"],
      "character_age_groups": ["角色名1 此刻明确处于的年龄阶段", "角色名2 的年龄阶段"]
    }
  ]
}

## 选择标准（必读）
1. **画面感优先**：选冲突爆发、动作高潮、表情极致、空间剧变的时刻；避开纯对话/旁白。
2. **2-3 个就够**：贪多会让关键帧成本爆炸，且每张都会被视觉小说反复用。
3. **不重复**：不同时刻的画面焦点 / 角色组合必须显著不同。
4. **target_characters 严格从原文出现过的角色名里挑**，不能编造；同一时刻最多 3 个角色。
5. **source_excerpt 必须是原文截取**，不能改写不能总结；找不到原文片段就别出这一条。
6. **不要写剧情摘要**：moment_summary 是「画面」不是「剧情梗概」，必须动词开头能想象出画面。
7. **character_emotions / character_outfits / character_poses / character_actions /
   character_injury_states / character_held_items 的 list 长度必须与
   target_characters 严格一致**，下标对齐：list[i] 描述 target_characters[i]。
   现场无法判断的字段允许填 "未知"，但不能省略整个 list。
8. **年龄是身份约束**：character_age_groups[i] 只能是 toddler/child/teen/young_adult/
   adult/middle_aged/senior。正文有回忆、时间跳跃或明确年龄时按这一时刻判断；没有明确
   证据写 ""，不得用原作常识猜测。

## 输出约束
- 只输出 JSON，不要 markdown 围栏，不要解释。
- moments 可以为空数组（如果这一章确实没什么画面感强的时刻）。
"""


# ----------------------- service -----------------------

class KeyframeMomentSelector:
    """P1.2 — 章节级关键帧时刻选择器。

    典型用法：
        selector = KeyframeMomentSelector()
        moments = await selector.select(chapter_content, outline_json, max_keyframes=3)
        # 然后传给 prompt_builder_service 拼 keyframe prompt
    """

    def __init__(self) -> None:
        self._client = None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # --------- llm client ---------

    @property
    def client(self):
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=KEYFRAME_MOMENT_API_KEY,
                pool_env=("KEYFRAME_MOMENT_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=KEYFRAME_MOMENT_BASE_URL,
            )
        return self._client

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """带重试 + 指数退避 + JSON response_format 的 LLM 调用。

        直接照抄 ``background_scene_analyzer_service._call_llm`` (L154-185)。
        """
        last_err: Optional[Exception] = None
        for attempt in range(KEYFRAME_MOMENT_RETRIES + 1):
            try:
                resp = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=resolve_request_model(KEYFRAME_MOMENT_MODEL),
                        temperature=KEYFRAME_MOMENT_TEMPERATURE,
                        max_tokens=KEYFRAME_MOMENT_MAX_TOKENS,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    ),
                    timeout=KEYFRAME_MOMENT_TIMEOUT,
                )
                content = resp.choices[0].message.content or "{}"
                return json.loads(content)
            except asyncio.TimeoutError as e:
                last_err = e
                logger.warning("keyframe_moment LLM timeout (attempt %d)", attempt + 1)
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning("keyframe_moment LLM json parse failed (attempt %d): %s", attempt + 1, e)
            except Exception as e:
                last_err = e
                logger.warning("keyframe_moment LLM call failed (attempt %d): %s", attempt + 1, e)
            if attempt < KEYFRAME_MOMENT_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(
            f"keyframe_moment LLM failed after {KEYFRAME_MOMENT_RETRIES + 1} attempts: {last_err}"
        )

    # --------- cache ---------

    def _cache_key(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        max_keyframes: int,
    ) -> str:
        h = hashlib.sha256()
        h.update(KEYFRAME_MOMENT_RECIPE_VERSION.encode("utf-8"))
        h.update(b"|")
        h.update(b"max_kf=")
        h.update(str(max_keyframes).encode("utf-8"))
        h.update(b"|outline=")
        h.update(json.dumps(chapter_outline, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        h.update(b"|txt=")
        h.update(chapter_content.encode("utf-8"))
        return h.hexdigest()

    def _cache_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        path = CACHE_DIR / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.warning("keyframe_moment cache read failed (%s): %s", path, e)
        return None

    def _cache_set(self, key: str, payload: List[Dict[str, Any]]) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path = CACHE_DIR / f"{key}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("keyframe_moment cache write failed: %s", e)

    # --------- helpers ---------

    @staticmethod
    def _trim_content(content: str, budget: int) -> str:
        """按字符预算截断正文，保留头尾（剧情高光常在转折处）。"""
        if len(content) <= budget:
            return content
        half = budget // 2
        head = content[:half]
        tail = content[-(budget - half):]
        return f"{head}\n…（中段省略 {len(content) - budget} 字）…\n{tail}"

    @staticmethod
    def _extract_outline_characters(chapter_outline: Dict[str, Any]) -> List[str]:
        """从章节大纲里抽出已知出场角色名，作为 LLM 输出的白名单。

        大纲的 ``characters`` 字段可能是 list[str] 也可能是 list[dict]，
        与 ``prompt_builder_service._extract_characters_for_keyframe`` 兼容。
        """
        raw = chapter_outline.get("characters") or []
        names: List[str] = []
        if not isinstance(raw, list):
            return names
        for c in raw[:10]:
            if isinstance(c, str):
                n = c.strip()
                if n:
                    names.append(n)
            elif isinstance(c, dict):
                n = (c.get("name") or c.get("name_cn") or "").strip()
                if n:
                    names.append(n)
        return names

    @staticmethod
    def _normalize_character_list(raw: Any, valid_names: List[str]) -> List[str]:
        """把 LLM 输出的 target_characters 规范化：

        - 去空白 / 去重（保序）
        - 若大纲提供了白名单，则过滤掉不在白名单里的角色（防 LLM 编造）
        - 截断到 ``MAX_CHARACTERS_PER_MOMENT``
        """
        if not isinstance(raw, list):
            return []
        valid_set = {n for n in valid_names if n}
        out: List[str] = []
        seen: set = set()
        for c in raw:
            if not isinstance(c, str):
                continue
            n = c.strip()
            if not n or n in seen:
                continue
            if valid_set and n not in valid_set:
                continue
            seen.add(n)
            out.append(n)
            if len(out) >= MAX_CHARACTERS_PER_MOMENT:
                break
        return out

    @staticmethod
    def _coerce_str(value: Any, limit: int = 300) -> str:
        if value is None:
            return ""
        return str(value).strip()[:limit]

    # --------- prompt assembly ---------

    def _build_user_prompt(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        max_keyframes: int,
    ) -> str:
        trimmed = self._trim_content(chapter_content, KEYFRAME_MOMENT_CONTENT_BUDGET)
        outline_block = json.dumps(chapter_outline, ensure_ascii=False, indent=2)
        return f"""<MaxKeyframes>{max_keyframes}</MaxKeyframes>

<ChapterOutline>
{outline_block or "(no outline provided)"}
</ChapterOutline>

<ChapterText>
{trimmed}
</ChapterText>

<OutputSchemaHint>
返回 JSON: {{"moments": [moment, ...]}}
每个 moment 字段：
- moment_summary (str, 中文, <=40 字, 动词开头, 例"林夜持剑指向群臣")
- target_characters (list[str], 角色名, 最多 3 个)
- visual_focus (str, 中文, <=50 字, 描述画面焦点和构图)
- source_excerpt (str, 中文, <=80 字, 原文片段)
- rationale (str, 中文, <=60 字, 为什么这一刻该被定格)
- character_age_groups (list[str], 与角色对齐；只允许 toddler/child/teen/young_adult/adult/middle_aged/senior 或空串)
</OutputSchemaHint>

只输出严格 JSON，不要 markdown 围栏，不要解释。
"""

    # --------- main entry ---------

    async def select(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        max_keyframes: int = 3,
    ) -> List[KeyframeMoment]:
        """从一章正文里挑 2-3 个最有画面感的时刻。

        Args:
            chapter_content: 章节正文（中文）。空串 / 过短 → 走 fallback。
            chapter_outline: 章节大纲 dict（含 ``title``/``scene``/``conflict``
                /``characters``/``emotion`` 等，与 ``prompt_builder_service``
                同 schema）。
            max_keyframes: 最多返回几个时刻，默认 3。

        Returns:
            List[KeyframeMoment]，已按 ``(moment_summary, target_characters)``
            dedup，且 ``len <= max_keyframes``。

            LLM 全失败 / 关闭 / 缺输入时返回 **空列表**，caller 应自行 fallback
            到 ``prompt_builder_service.build_keyframe_prompts`` 的旧逻辑
            （单关键帧 = 章节标题/场景）。
        """
        if max_keyframes <= 0 or not chapter_content or len(chapter_content) < 200:
            logger.info(
                "keyframe_moment skip (max_kf=%d, content_len=%d)",
                max_keyframes, len(chapter_content or ""),
            )
            return []

        if not KEYFRAME_MOMENT_ENABLED or not api_key_available(
            KEYFRAME_MOMENT_API_KEY,
            pool_env=("KEYFRAME_MOMENT_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info(
                "keyframe_moment disabled (no API key or KEYFRAME_AUTO_SELECT=0), "
                "caller should fallback to prompt_builder_service",
            )
            return []

        if not isinstance(chapter_outline, dict):
            chapter_outline = {}

        valid_names = self._extract_outline_characters(chapter_outline)

        # cache hit
        key = self._cache_key(chapter_content, chapter_outline, max_keyframes)
        cached = self._cache_get(key)
        if cached is not None:
            logger.info(
                "keyframe_moment cache hit (max_kf=%d, %d moments)",
                max_keyframes, len(cached),
            )
            return [self._dict_to_moment(d, valid_names) for d in cached]

        user_prompt = self._build_user_prompt(chapter_content, chapter_outline, max_keyframes)
        try:
            data = await asyncio.wait_for(
                self._call_llm(SYSTEM_PROMPT, user_prompt),
                timeout=KEYFRAME_MOMENT_TIMEOUT * 2,
            )
        except asyncio.TimeoutError:
            logger.warning("keyframe_moment timed out")
            return []
        except Exception as e:
            logger.warning("keyframe_moment failed: %s", e)
            return []

        raw_moments = data.get("moments") if isinstance(data, dict) else None
        if not isinstance(raw_moments, list):
            raw_moments = []

        moments: List[KeyframeMoment] = []
        seen_keys: set = set()
        for m in raw_moments:
            if not isinstance(m, dict):
                continue
            summary = self._coerce_str(m.get("moment_summary"), limit=120)
            source_excerpt = self._coerce_str(m.get("source_excerpt"), limit=300)
            # source_excerpt 必须有内容且能在原文里找到（允许模糊首尾对齐）。
            if not summary or not source_excerpt:
                continue
            if source_excerpt[:20] not in chapter_content and source_excerpt[-20:] not in chapter_content:
                logger.info(
                    "keyframe_moment drop moment (source_excerpt not in chapter): %r",
                    source_excerpt[:40],
                )
                continue
            chars = self._normalize_character_list(m.get("target_characters"), valid_names)
            moment = KeyframeMoment(
                moment_summary=summary,
                target_characters=chars,
                visual_focus=self._coerce_str(m.get("visual_focus"), limit=200),
                source_excerpt=source_excerpt,
                rationale=self._coerce_str(m.get("rationale"), limit=200),
                character_emotions=self._normalize_state_list(
                    m.get("character_emotions"), m.get("target_characters"),
                ),
                character_outfits=self._normalize_state_list(
                    m.get("character_outfits"), m.get("target_characters"),
                ),
                character_poses=self._normalize_state_list(
                    m.get("character_poses"), m.get("target_characters"),
                ),
                character_actions=self._normalize_state_list(
                    m.get("character_actions"), m.get("target_characters"),
                ),
                character_injury_states=self._normalize_state_list(
                    m.get("character_injury_states"), m.get("target_characters"),
                ),
                character_held_items=self._normalize_state_list(
                    m.get("character_held_items"), m.get("target_characters"),
                ),
                character_age_groups=self._normalize_age_group_list(
                    m.get("character_age_groups"), m.get("target_characters"),
                ),
            )
            fp = moment.fingerprint()
            if fp in seen_keys:
                continue
            seen_keys.add(fp)
            moments.append(moment)
            if len(moments) >= max_keyframes:
                break

        # 写缓存（仅 persist 通过校验的）
        self._cache_set(key, [asdict(m) for m in moments])

        logger.info(
            "keyframe_moment produced %d moments (max_kf=%d, content_len=%d)",
            len(moments), max_keyframes, len(chapter_content),
        )
        return moments

    @staticmethod
    def _dict_to_moment(d: Dict[str, Any], valid_names: List[str]) -> KeyframeMoment:
        """把缓存里的 dict 还原成 KeyframeMoment。"""
        return KeyframeMoment(
            moment_summary=str(d.get("moment_summary") or ""),
            target_characters=KeyframeMomentSelector._normalize_character_list(
                d.get("target_characters"), valid_names,
            ),
            visual_focus=str(d.get("visual_focus") or ""),
            source_excerpt=str(d.get("source_excerpt") or ""),
            rationale=str(d.get("rationale") or ""),
            character_emotions=KeyframeMomentSelector._normalize_state_list(
                d.get("character_emotions"), d.get("target_characters"),
            ),
            character_outfits=KeyframeMomentSelector._normalize_state_list(
                d.get("character_outfits"), d.get("target_characters"),
            ),
            character_poses=KeyframeMomentSelector._normalize_state_list(
                d.get("character_poses"), d.get("target_characters"),
            ),
            character_actions=KeyframeMomentSelector._normalize_state_list(
                d.get("character_actions"), d.get("target_characters"),
            ),
            character_injury_states=KeyframeMomentSelector._normalize_state_list(
                d.get("character_injury_states"), d.get("target_characters"),
            ),
            character_held_items=KeyframeMomentSelector._normalize_state_list(
                d.get("character_held_items"), d.get("target_characters"),
            ),
            character_age_groups=KeyframeMomentSelector._normalize_age_group_list(
                d.get("character_age_groups"), d.get("target_characters"),
            ),
        )

    @staticmethod
    def _normalize_state_list(
        raw: Any,
        names_ref: Any,
    ) -> Optional[List[str]]:
        """Coerce clothing-state list to List[str], length-aligned with names."""
        if raw is None:
            return None
        if not isinstance(raw, list):
            return None
        out: List[str] = []
        names_count = (
            len(names_ref) if isinstance(names_ref, list) else 0
        )
        for i, item in enumerate(raw):
            s = str(item).strip() if item is not None else ""
            out.append(s[:60])  # truncate absurd outputs
        if names_count and len(out) < names_count:
            out.extend([""] * (names_count - len(out)))
        if names_count and len(out) > names_count:
            out = out[:names_count]
        return out

    @staticmethod
    def _normalize_age_group_list(
        raw: Any,
        names_ref: Any,
    ) -> Optional[List[str]]:
        states = KeyframeMomentSelector._normalize_state_list(raw, names_ref)
        if states is None:
            return None
        from app.services.character_age_contract import normalize_age_group

        out: List[str] = []
        for item in states:
            text = str(item or "").strip()
            if not text or text.lower() in {"unknown", "unspecified", "未知", "不明"}:
                out.append("")
            else:
                out.append(normalize_age_group(text))
        return out


# ----------------------- module-level singleton（轻量调用方直接 import 用）-----------------------

_default_selector: Optional[KeyframeMomentSelector] = None


def get_default_selector() -> KeyframeMomentSelector:
    global _default_selector
    if _default_selector is None:
        _default_selector = KeyframeMomentSelector()
    return _default_selector

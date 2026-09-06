"""
Portrait Demand Analyzer (P1.1)

输入：project_id + chapter_indices + story_bible
输出：List[PortraitDemand]，告诉 ``generate_all_portraits``「这一章里哪个角色
      出现了哪种情绪/装束/姿态，需要为他生一张立绘变体」。

职责：
- 读 ``ChapterContent.content`` + ``StoryBible.characters`` 角色卡。
- LLM 分析「哪一段、哪个角色、需要哪个 emotion/outfit/pose 变体」。
- 输出按 ``(character_id, emotion, outfit, pose)`` 全局 dedup，避免重复合成。
- 失败时 fallback 到 ``prompt_builder_service`` 的默认 5 档变体（与旧用户选项
  模式等价），保证链路不死。

调用形态参考 ``background_scene_analyzer_service.py`` 的 ``_call_llm``：
  - ``response_format={"type": "json_object"}`` 强制 JSON 输出
  - ``asyncio.wait_for`` 外层超时兜底
  - 指数退避重试 (``0.5 * 2**attempt``)
  - sha256 缓存 → ``backend/.cache/portrait_demand/<hash>.json``

公开入口：``await PortraitDemandAnalyzer().analyze(project_id, chapter_indices, story_bible)``
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.services.text_llm_config import DEFAULT_TEXT_LLM_MODEL, resolve_request_model, text_llm_api_key, text_llm_base_url, text_llm_model
from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available

logger = logging.getLogger("portrait_demand_analyzer")

# ----------------------- env（与 background_scene_analyzer 一致的 fallback 链）-----------------------

PORTRAIT_DEMAND_ENABLED = os.getenv("PORTRAIT_DEMAND_ENABLED", "true").lower() == "true"
PORTRAIT_DEMAND_MODEL = text_llm_model("PORTRAIT_DEMAND_MODEL", "BG_ANALYZER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
PORTRAIT_DEMAND_API_KEY = text_llm_api_key("PORTRAIT_DEMAND_API_KEY", "BG_ANALYZER_API_KEY", "REWRITER_API_KEY")
PORTRAIT_DEMAND_BASE_URL = text_llm_base_url("PORTRAIT_DEMAND_BASE_URL", "BG_ANALYZER_BASE_URL", "REWRITER_BASE_URL")
PORTRAIT_DEMAND_TIMEOUT = float(os.getenv("PORTRAIT_DEMAND_TIMEOUT", "90.0"))
PORTRAIT_DEMAND_TEMPERATURE = float(os.getenv("PORTRAIT_DEMAND_TEMPERATURE", "0.2"))
PORTRAIT_DEMAND_MAX_TOKENS = int(os.getenv("PORTRAIT_DEMAND_MAX_TOKENS", "4000"))
PORTRAIT_DEMAND_RETRIES = int(os.getenv("PORTRAIT_DEMAND_RETRIES", "1"))

# 单章一次 LLM 调用，章节正文超过此字符数则截断（防爆 token）。
PORTRAIT_DEMAND_CONTENT_BUDGET = int(os.getenv("PORTRAIT_DEMAND_CONTENT_BUDGET", "6000"))

CACHE_DIR = Path(os.getenv(
    "PORTRAIT_DEMAND_CACHE_DIR",
    str(Path(__file__).parent.parent.parent / ".cache" / "portrait_demand"),
))

# 单角色最大变体数（防止 LLM 失控给同一角色刷 20 张），env 可调。
MAX_DEMANDS_PER_CHARACTER = int(os.getenv("PORTRAIT_DEMAND_MAX_PER_CHARACTER", "8"))
PORTRAIT_DEMAND_RECIPE_VERSION = "content-character-entry-v4-age-contract"

_GENERIC_CHARACTER_LABELS = {
    "npc", "narrator", "旁白", "路人", "路人甲", "路人乙", "士兵", "守卫",
    "侍卫", "军士", "官兵", "群众", "众人", "旁人", "村民", "店员", "侍者",
    "仆人", "丫鬟", "老人", "男人", "女人", "男孩", "女孩", "某人",
}


# ----------------------- dataclass -----------------------

@dataclass
class PortraitDemand:
    """单条立绘需求。

    ``character_id`` 与 ``prompt_builder_service.generate_character_id`` 同算法
    生成（``character_name + project_id`` 的 md5 短哈希），方便下游直接对齐
    Asset 表的 ``character_id`` 列。
    """
    character_id: str
    character_name: str
    emotion: str
    outfit: str
    pose: str
    source_chapter_index: int
    age_group: str = "young_adult"
    age_source: str = "character_profile"
    source_excerpt: str = ""
    rationale: str = ""

    def fingerprint(self) -> Tuple[str, str, str, str, str]:
        """Age is an identity variant and therefore part of the dedup key."""
        return (
            self.character_id,
            self.age_group,
            self.emotion,
            self.outfit,
            self.pose,
        )


# ----------------------- system prompt -----------------------

SYSTEM_PROMPT = """你是通用小说视觉导演。给你一章小说正文 + 可选角色卡列表，你的工作是
决定「这一章里哪些具体人物需要立绘，以及需要哪些情绪/装束/姿态变体」。
不得依赖任何特定作品、时代、语言或预设人物名单。

## 输出 JSON 结构
{
  "demands": [
    {
      "character_name": "角色卡中的名字，或正文明确引入且原文确实出现的具体姓名",
      "emotion": "情绪标签，英文小写，例 happy/sad/angry/surprise/neutral/fear/disgust/contempt/determined",
      "outfit": "装束标签，英文小写，例 default/casual/formal/mourning_combat/wounded",
      "pose": "姿态标签，英文小写，例 standing/sitting/combat/reaching/running/kneeling",
      "age_group": "画面中该角色明确处于的年龄阶段，只能是 toddler/child/teen/young_adult/adult/middle_aged/senior",
      "source_excerpt": "原文里触发这条需求的句子片段（中文，<=80 字，必须原文截取，不能改写）",
      "rationale": "为什么这一段需要这个变体（中文，<=60 字，说清情绪/装束/姿态的视觉差异）"
    }
  ]
}

## 选择标准（必读）
1. **基础立绘不可漏**：每个实际说话、首次进入画面、或执行清晰可见动作的命名人物，
   至少输出一条 neutral/default/standing demand，即使这个人物尚不在角色卡里。
2. **新人物必须有原文证据**：角色卡外的人名必须逐字出现在 ChapterText，
   source_excerpt 也必须逐字截自原文。不能根据常识补人物。
3. **匿名群体不做立绘**：路人、士兵、守卫、群众、众人、旁人、NPC 等泛称不输出；
   有稳定专名的个体（例如“顾青”“陈伯”“Alice”）不是匿名群体。
4. **只加有意义的变体**：除基础立绘外，只为显著情绪、装束或姿态变化增加 demand。
5. **优先剧情高光**：冲突、惊变、和解、对决等可增加相应变体。
6. **不要重复**：同一个角色的同一个 (emotion, outfit, pose) 组合只出 1 条。
7. **单章单角色不超过 4 条**：基础立绘也计入。
8. **不写剧情摘要**：source_excerpt 必须是原文截取，rationale 只说明说话/入场/动作或视觉差异。
9. **不要改角色身份**：已有角色卡的 gender/appearance 是稳定身份约束。
10. **年龄按本章时期判断**：同一人物可在回忆/时间跳跃中处于不同年龄阶段。正文有明确
    年龄、少年/青年/中年/老年时期证据时按正文输出；没有明确证据时沿用角色卡 age_group，
    不得凭原作常识猜测。

## 输出约束
- 只输出 JSON，不要 markdown 围栏，不要解释。
- demands 可以为空数组（如果这一章确实没什么立绘变体需求）。
"""


# ----------------------- service -----------------------

class PortraitDemandAnalyzer:
    """P1.1 — 章节级立绘需求分析器。

    典型用法：
        analyzer = PortraitDemandAnalyzer(db_session)
        demands = await analyzer.analyze(project_id, [1,2,3], story_bible_dict)
        # 然后传给 asset_management_service.generate_all_portraits(variations=demands)
    """

    def __init__(self, db: Optional[Session] = None) -> None:
        self._db = db
        self._client = None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # --------- db session ---------

    @property
    def db(self) -> Session:
        if self._db is None:
            # 与 asset_management_service 同样的懒加载：caller 不传 db 时自取一个。
            from app.database import SessionLocal
            self._db = SessionLocal()
        return self._db

    def _get_chapter_content(self, project_id: int, chapter_index: int) -> Optional[str]:
        """读 ChapterContent.content，章节不存在返回 None。"""
        from app.models import ChapterContent
        row = self.db.query(ChapterContent).filter(
            ChapterContent.project_id == project_id,
            ChapterContent.chapter_index == chapter_index,
        ).first()
        if not row or not row.content:
            return None
        return row.content

    # --------- llm client ---------

    @property
    def client(self):
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=PORTRAIT_DEMAND_API_KEY,
                pool_env=("PORTRAIT_DEMAND_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=PORTRAIT_DEMAND_BASE_URL,
            )
        return self._client

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """带重试 + 指数退避 + JSON response_format 的 LLM 调用。

        直接照抄 ``background_scene_analyzer_service._call_llm`` (L154-185)。
        """
        last_err: Optional[Exception] = None
        for attempt in range(PORTRAIT_DEMAND_RETRIES + 1):
            try:
                resp = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=resolve_request_model(PORTRAIT_DEMAND_MODEL),
                        temperature=PORTRAIT_DEMAND_TEMPERATURE,
                        max_tokens=PORTRAIT_DEMAND_MAX_TOKENS,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    ),
                    timeout=PORTRAIT_DEMAND_TIMEOUT,
                )
                content = resp.choices[0].message.content or "{}"
                return json.loads(content)
            except asyncio.TimeoutError as e:
                last_err = e
                logger.warning("portrait_demand LLM timeout (attempt %d)", attempt + 1)
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning("portrait_demand LLM json parse failed (attempt %d): %s", attempt + 1, e)
            except Exception as e:
                last_err = e
                logger.warning("portrait_demand LLM call failed (attempt %d): %s", attempt + 1, e)
            if attempt < PORTRAIT_DEMAND_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(
            f"portrait_demand LLM failed after {PORTRAIT_DEMAND_RETRIES + 1} attempts: {last_err}"
        )

    # --------- cache ---------

    def _cache_key(
        self,
        project_id: int,
        chapter_index: int,
        chapter_content: str,
        characters_block: str,
    ) -> str:
        h = hashlib.sha256()
        h.update(PORTRAIT_DEMAND_RECIPE_VERSION.encode("utf-8"))
        h.update(b"|")
        h.update(str(project_id).encode("utf-8"))
        h.update(b"|ci=")
        h.update(str(chapter_index).encode("utf-8"))
        h.update(b"|txt=")
        h.update(chapter_content.encode("utf-8"))
        h.update(b"|chars=")
        h.update(characters_block.encode("utf-8"))
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
            logger.warning("portrait_demand cache read failed (%s): %s", path, e)
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
            logger.warning("portrait_demand cache write failed: %s", e)

    # --------- helpers ---------

    @staticmethod
    def _character_id(character_name: str, project_id: int, resolver: Any = None) -> str:
        """与 ``prompt_builder_service.generate_character_id`` 一致的算法，
        保证下游 ``Asset.character_id`` 列对齐。

        若传入 ``resolver``（CanonicalCharacterResolver），优先用 resolver 解析
        alias / canonical_name → character_id；否则降级 md5。
        """
        if resolver is not None:
            try:
                return resolver.resolve(character_name).character_id
            except Exception:
                pass
        import hashlib as _hashlib
        data = f"{character_name}|{project_id}"
        return _hashlib.md5(data.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _normalize_label(value: Any, default: str) -> str:
        """LLM 输出的 emotion/outfit/pose 规范化：去空白、转小写、空值兜底。"""
        if value is None:
            return default
        s = str(value).strip().lower()
        if not s or s in {"none", "null", "未知"}:
            return default
        return s

    @staticmethod
    def _is_specific_character_name(
        name: str,
        chapter_content: str,
        source_excerpt: str = "",
    ) -> bool:
        """Allow story-card names and newly introduced proper names safely.

        A new identity must be visible in the source text. Anonymous crowd or
        job labels remain background-only so the system does not manufacture
        dozens of interchangeable portraits.
        """
        clean = str(name or "").strip()
        if not clean or len(clean) > 40:
            return False
        if clean.lower() in _GENERIC_CHARACTER_LABELS:
            return False
        if any(ch in clean for ch in "\n\r\t，。！？；：,.!?;:\"“”'‘’（）()[]{}"):
            return False
        if clean not in (chapter_content or ""):
            return False
        excerpt = str(source_excerpt or "").strip()
        if excerpt and excerpt not in chapter_content:
            return False
        return True

    @staticmethod
    def _normalize_gender(value: Any) -> str:
        """归一化角色卡性别，供 LLM 需求分析时保留 identity 约束。"""
        from app.services.prompt_builder_service import prompt_builder_service
        return prompt_builder_service.normalize_gender(value)

    @classmethod
    def _extract_gender_from_card(cls, card: Dict[str, Any]) -> str:
        """显式字段优先；缺失时用外貌/身份关键词弱推断。"""
        from app.services.prompt_builder_service import prompt_builder_service
        return prompt_builder_service._extract_gender(card)

    @staticmethod
    def _build_characters_block(story_bible: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        """从 story_bible 抽角色卡 list，并构造给 LLM 的简短文本块。"""
        raw_chars = story_bible.get("characters") or []
        # 兼容 list[dict] / list[str]
        cards: List[Dict[str, Any]] = []
        for c in raw_chars:
            if isinstance(c, dict):
                cards.append(c)
            elif isinstance(c, str):
                cards.append({"name": c})
        if not cards:
            return "", []
        lines = []
        for c in cards:
            name = c.get("name") or c.get("name_cn") or ""
            appearance = c.get("appearance", "")
            role = c.get("role") or c.get("identity") or ""
            gender = PortraitDemandAnalyzer._extract_gender_from_card(c)
            profile = c.get("visual_profile") if isinstance(c.get("visual_profile"), dict) else {}
            age_group = profile.get("age_group") or c.get("age_group") or "young_adult"
            lines.append(
                f"- name={name} | gender={gender or 'unknown'} | age_group={age_group} "
                f"| appearance={appearance} | role={role}"
            )
        return "\n".join(lines), cards

    @staticmethod
    def _trim_content(content: str, budget: int) -> str:
        """按字符预算截断正文，保留头尾（剧情高光常在转折处）。"""
        if len(content) <= budget:
            return content
        half = budget // 2
        head = content[:half]
        tail = content[-(budget - half):]
        return f"{head}\n…（中段省略 {len(content) - budget} 字）…\n{tail}"

    # --------- prompt assembly ---------

    def _build_user_prompt(
        self,
        project_id: int,
        chapter_index: int,
        chapter_content: str,
        characters_block: str,
    ) -> str:
        trimmed = self._trim_content(chapter_content, PORTRAIT_DEMAND_CONTENT_BUDGET)
        return f"""<ProjectId>{project_id}</ProjectId>
<ChapterIndex>{chapter_index}</ChapterIndex>

<CharacterCards>
{characters_block or "(no character cards provided)"}
</CharacterCards>

<ChapterText>
{trimmed}
</ChapterText>

<OutputSchemaHint>
返回 JSON: {{"demands": [demand, ...]}}
每个 demand 字段：
- character_name (str, 来自角色卡；或正文逐字出现的具体人名)
- emotion (str, 英文小写, 例 happy/sad/angry/surprise/neutral/fear/disgust/contempt/determined)
- outfit (str, 英文小写, 例 default/casual/formal/mourning/combat/wounded)
- pose (str, 英文小写, 例 standing/sitting/combat/reaching/running/kneeling)
- age_group (str, toddler/child/teen/young_adult/adult/middle_aged/senior；按本章明确时期)
- source_excerpt (str, 原文中文片段, <=80 字)
- rationale (str, 中文, <=60 字, 说清视觉差异)
必须覆盖每个实际说话、首次进入画面或执行可见动作的命名人物；
命名新人物至少输出 neutral/default/standing。匿名泛称不要输出。
</OutputSchemaHint>

只输出严格 JSON，不要 markdown 围栏，不要解释。
"""

    # --------- per-chapter analyze ---------

    async def _analyze_chapter(
        self,
        project_id: int,
        chapter_index: int,
        chapter_content: str,
        story_bible: Dict[str, Any],
        resolver: Any = None,
    ) -> List[PortraitDemand]:
        """单章一次 LLM 调用 → 规范化、过滤、限频 → List[PortraitDemand]。"""
        characters_block, cards = self._build_characters_block(story_bible)
        valid_names = {
            (c.get("name") or c.get("name_cn") or "").strip()
            for c in cards
        }
        valid_names.discard("")
        cards_by_name = {
            (c.get("name") or c.get("name_cn") or "").strip(): c
            for c in cards
            if (c.get("name") or c.get("name_cn") or "").strip()
        }

        # cache hit
        key = self._cache_key(project_id, chapter_index, chapter_content, characters_block)
        cached = self._cache_get(key)
        if cached is not None:
            logger.info(
                "portrait_demand cache hit project=%d chapter=%d (%d demands)",
                project_id, chapter_index, len(cached),
            )
            return [
                self._dict_to_demand(d, project_id, chapter_index, resolver)
                for d in cached
            ]

        # 正文过短 / API 未配置 → 跳过本章（caller 会用全局 fallback）。
        # 无角色卡不再早退：正文中新出现的命名人物也必须能建立立绘身份。
        if len(chapter_content) < 400:
            logger.info(
                "portrait_demand skip chapter=%d (cards=%d, content_len=%d)",
                chapter_index, len(cards), len(chapter_content),
            )
            return []

        if not PORTRAIT_DEMAND_ENABLED or not api_key_available(
            PORTRAIT_DEMAND_API_KEY,
            pool_env=("PORTRAIT_DEMAND_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info("portrait_demand disabled (no API key), skip chapter=%d", chapter_index)
            return []

        user_prompt = self._build_user_prompt(
            project_id, chapter_index, chapter_content, characters_block,
        )
        try:
            data = await asyncio.wait_for(
                self._call_llm(SYSTEM_PROMPT, user_prompt),
                timeout=PORTRAIT_DEMAND_TIMEOUT * 2,
            )
        except asyncio.TimeoutError:
            logger.warning("portrait_demand timed out, chapter=%d", chapter_index)
            return []
        except Exception as e:
            logger.warning("portrait_demand failed chapter=%d: %s", chapter_index, e)
            return []

        raw_demands = data.get("demands") if isinstance(data, dict) else None
        if not isinstance(raw_demands, list):
            raw_demands = []

        demands: List[PortraitDemand] = []
        seen_keys: set = set()
        per_char_count: Dict[str, int] = {}
        for d in raw_demands:
            if not isinstance(d, dict):
                continue
            name = str(d.get("character_name") or "").strip()
            source_excerpt = str(d.get("source_excerpt") or "").strip()[:200]
            # 角色卡人物直接允许；新人物必须有正文逐字证据且不能是匿名泛称。
            if name not in valid_names and not self._is_specific_character_name(
                name,
                chapter_content,
                source_excerpt,
            ):
                logger.info(
                    "portrait_demand drop ungrounded/generic char %r (chapter=%d)",
                    name, chapter_index,
                )
                continue
            resolved = resolver.resolve(name) if resolver is not None else None
            source_card = cards_by_name.get(name) or {}
            if resolved is not None and resolved.confidence >= 0.6:
                name = resolved.canonical_name
                source_card = cards_by_name.get(name) or source_card
            cid = self._character_id(name, project_id, resolver)
            emotion = self._normalize_label(d.get("emotion"), "neutral")
            outfit = self._normalize_label(d.get("outfit"), "default")
            pose = self._normalize_label(d.get("pose"), "standing")
            from app.services.character_age_contract import (
                infer_age_group_from_text,
                normalize_age_group,
            )
            profile = (
                source_card.get("visual_profile")
                if isinstance(source_card.get("visual_profile"), dict)
                else {}
            )
            static_age = normalize_age_group(
                profile.get("age_group") or source_card.get("age_group") or "young_adult"
            )
            explicit_age = str(d.get("age_group") or "").strip()
            evidence_text = f"{source_excerpt} {d.get('rationale') or ''}"
            age_group = (
                normalize_age_group(explicit_age, static_age)
                if explicit_age
                else infer_age_group_from_text(evidence_text, static_age)
            )
            age_source = (
                "chapter_text"
                if explicit_age or age_group != static_age
                else "character_profile"
            )
            fp = (cid, age_group, emotion, outfit, pose)
            if fp in seen_keys:
                continue
            # 单角色限频（防止 LLM 失控刷同一角色的变体）
            if per_char_count.get(cid, 0) >= MAX_DEMANDS_PER_CHARACTER:
                continue
            seen_keys.add(fp)
            per_char_count[cid] = per_char_count.get(cid, 0) + 1
            demands.append(PortraitDemand(
                character_id=cid,
                character_name=name,
                emotion=emotion,
                outfit=outfit,
                pose=pose,
                source_chapter_index=chapter_index,
                age_group=age_group,
                age_source=age_source,
                source_excerpt=source_excerpt,
                rationale=str(d.get("rationale") or "")[:200],
            ))

        # New named characters need a drawable base even when the LLM only
        # returned a high-emotion variant. Add exactly one neutral identity
        # anchor; later semantic selection can choose a matching variant.
        newly_discovered = {
            demand.character_name
            for demand in demands
            if demand.character_name not in valid_names
        }
        for name in sorted(newly_discovered):
            resolved = resolver.resolve(name) if resolver is not None else None
            if resolved is not None and resolved.confidence >= 0.6:
                name = resolved.canonical_name
            cid = self._character_id(name, project_id, resolver)
            first = next(d for d in demands if d.character_name == name)
            base_fp = (cid, first.age_group, "neutral", "default", "standing")
            if base_fp in seen_keys:
                continue
            seen_keys.add(base_fp)
            demands.append(PortraitDemand(
                character_id=cid,
                character_name=name,
                emotion="neutral",
                outfit="default",
                pose="standing",
                source_chapter_index=chapter_index,
                age_group=first.age_group,
                age_source=first.age_source,
                source_excerpt=first.source_excerpt,
                rationale="正文中新命名人物的首次说话、入场或可见动作基础立绘",
            ))

        # 写缓存（仅 persist 通过校验的）
        self._cache_set(key, [asdict(d) for d in demands])

        logger.info(
            "portrait_demand chapter=%d produced %d demands",
            chapter_index, len(demands),
        )
        return demands

    @staticmethod
    def _dict_to_demand(
        d: Dict[str, Any],
        project_id: int,
        chapter_index: int,
        resolver: Any = None,
    ) -> PortraitDemand:
        """把缓存里的 dict 还原成 PortraitDemand。

        缓存里可能缺 character_id（旧版），用 character_name 现算兜底。
        """
        name = str(d.get("character_name") or "").strip()
        resolved = resolver.resolve(name) if resolver is not None else None
        if resolved is not None and resolved.confidence >= 0.6:
            name = resolved.canonical_name
            cid = resolved.character_id
        else:
            cid = str(d.get("character_id") or "") or PortraitDemandAnalyzer._character_id(name, project_id)
        return PortraitDemand(
            character_id=cid,
            character_name=name,
            emotion=str(d.get("emotion") or "neutral"),
            outfit=str(d.get("outfit") or "default"),
            pose=str(d.get("pose") or "standing"),
            source_chapter_index=int(d.get("source_chapter_index", chapter_index)),
            age_group=str(d.get("age_group") or "young_adult"),
            age_source=str(d.get("age_source") or "character_profile"),
            source_excerpt=str(d.get("source_excerpt") or ""),
            rationale=str(d.get("rationale") or ""),
        )

    # --------- fallback ---------

    def _fallback_default_demands(
        self,
        project_id: int,
        story_bible: Dict[str, Any],
    ) -> List[PortraitDemand]:
        """LLM 不可用 / 无章节内容时的兜底：复用 prompt_builder_service 默认 5 档变体。

        与旧用户选项模式的默认值等价，保证链路不死。
        """
        _, cards = self._build_characters_block(story_bible)
        if not cards:
            return []
        default_variations = [
            ("neutral", "default", "standing"),
            ("happy", "default", "standing"),
            ("angry", "default", "combat"),
            ("sad", "mourning", "standing"),
            ("surprise", "default", "reaching"),
        ]
        demands: List[PortraitDemand] = []
        for c in cards:
            name = (c.get("name") or c.get("name_cn") or "").strip()
            if not name:
                continue
            cid = self._character_id(name, project_id)
            from app.services.character_age_contract import normalize_age_group
            profile = c.get("visual_profile") if isinstance(c.get("visual_profile"), dict) else {}
            age_group = normalize_age_group(profile.get("age_group") or c.get("age_group"))
            for emotion, outfit, pose in default_variations:
                demands.append(PortraitDemand(
                    character_id=cid,
                    character_name=name,
                    emotion=emotion,
                    outfit=outfit,
                    pose=pose,
                    source_chapter_index=-1,  # 标记为 fallback
                    age_group=age_group,
                    age_source="character_profile",
                    rationale="(fallback: analyzer unavailable, using default 5 variants)",
                ))
        logger.info(
            "portrait_demand fallback: %d demands across %d characters (project=%d)",
            len(demands), len(cards), project_id,
        )
        return demands

    # --------- main entry ---------

    async def analyze(
        self,
        project_id: int,
        chapter_indices: Sequence[int],
        story_bible: Dict[str, Any],
    ) -> List[PortraitDemand]:
        """分析多章 → 全局 dedup → 输出 List[PortraitDemand]。

        Args:
            project_id: 项目 ID
            chapter_indices: 待分析的章节序号列表（如 [1, 2, 3]）
            story_bible: Story Bible dict（含 ``characters`` 列表，元素是 dict 或 str）

        Returns:
            List[PortraitDemand]，已按 ``(character_id, emotion, outfit, pose)`` dedup。
            LLM 全失败 / 无任何章节内容时返回 fallback 默认 5 档变体。
        """
        from app.services.canonical_character_resolver import CanonicalCharacterResolver
        resolver = CanonicalCharacterResolver(
            db=None,
            project_id=project_id,
            story_bible=story_bible,
        )

        if not chapter_indices:
            return self._fallback_default_demands(project_id, story_bible)

        # 早退：API key 缺失直接 fallback（避免无谓地循环章节）
        if not PORTRAIT_DEMAND_ENABLED or not api_key_available(
            PORTRAIT_DEMAND_API_KEY,
            pool_env=("PORTRAIT_DEMAND_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info("portrait_demand disabled, fallback to default variants (project=%d)", project_id)
            return self._fallback_default_demands(project_id, story_bible)

        chapter_indices = sorted(set(int(i) for i in chapter_indices))
        per_chapter_contents: List[Tuple[int, str]] = []
        for ci in chapter_indices:
            content = self._get_chapter_content(project_id, ci)
            if content and len(content) >= 400:
                per_chapter_contents.append((ci, content))

        if not per_chapter_contents:
            # 一章内容都没有 → fallback
            logger.info(
                "portrait_demand no usable chapter content, fallback (project=%d)",
                project_id,
            )
            return self._fallback_default_demands(project_id, story_bible)

        start = time.time()
        # 逐章 LLM 调用（章节之间天然串行，避免并发打爆 LLM 限流；
        # 并发上限交由 caller 在更上层调度）
        all_demands: List[PortraitDemand] = []
        for ci, content in per_chapter_contents:
            try:
                chapter_demands = await self._analyze_chapter(
                    project_id, ci, content, story_bible, resolver,
                )
            except Exception as e:
                logger.warning(
                    "portrait_demand chapter=%d unexpected error: %s", ci, e,
                )
                chapter_demands = []
            all_demands.extend(chapter_demands)

        # 全局 dedup by (character_id, emotion, outfit, pose)
        seen: set = set()
        deduped: List[PortraitDemand] = []
        for d in all_demands:
            fp = d.fingerprint()
            if fp in seen:
                continue
            seen.add(fp)
            deduped.append(d)

        # 全 LLM 失败 / 全无 demand → fallback
        if not deduped:
            logger.warning(
                "portrait_demand all chapters produced 0 demands, fallback (project=%d)",
                project_id,
            )
            return self._fallback_default_demands(project_id, story_bible)

        logger.info(
            "portrait_demand done project=%d chapters=%d demands=%d (deduped from %d) elapsed=%.2fs",
            project_id, len(per_chapter_contents), len(deduped), len(all_demands),
            time.time() - start,
        )
        return deduped


# ----------------------- module-level singleton（轻量调用方直接 import 用）-----------------------

_default_analyzer: Optional[PortraitDemandAnalyzer] = None


def get_default_analyzer() -> PortraitDemandAnalyzer:
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = PortraitDemandAnalyzer()
    return _default_analyzer

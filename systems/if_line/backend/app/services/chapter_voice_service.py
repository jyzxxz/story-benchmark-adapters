"""
Chapter Voice Service (P2.1)

输入：``project_id`` + ``chapter_index``
输出：``List[VoiceLine]``，把一章正文切成有序的「旁白 / 对白」片段，每段都
      调 ``tts_service.synthesize`` 合成音频，落库成
      ``Asset(asset_type="voice_line", chapter_index, character_id, emotion,
              prompt=text, image_url=audio_url)``。

职责（blueprint P2.1）：
1. 读 ``ChapterContent.content`` + ``StoryBible.characters`` 角色卡（音色来源）。
2. LLM 切有序 ``VoiceLine``：
   ``{kind: narration|dialogue, speaker_name?, text, character_voice?, emotion?}``。
3. **并发**调 ``tts_service.synthesize``（复用 ``image_generation_service``
   的 batch 切批模式 + ``tts_service`` 自带的音频缓存与信号量）。
4. 落库 ``Asset(asset_type="voice_line", ...)``，``prompt`` 存原文 text 方便
   P3 装配时按 text 匹配 vngraph dialogue 节点。

调用形态参考 ``background_scene_analyzer_service.py`` 的 ``_call_llm``：
  - ``response_format={"type": "json_object"}`` 强制 JSON 输出
  - ``asyncio.wait_for`` 外层超时兜底
  - 指数退避重试 (``0.5 * 2**attempt``)
  - sha256 缓存 → ``backend/.cache/chapter_voice/<hash>.json``（仅缓存 LLM
    切片结果；TTS 缓存由 ``tts_service`` 自己管，不在这里重复）

公开入口：
    await ChapterVoiceService(db).generate_chapter_voices(project_id, chapter_index)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.services.text_llm_config import DEFAULT_TEXT_LLM_MODEL, resolve_request_model, text_llm_api_key, text_llm_base_url, text_llm_model
from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available

logger = logging.getLogger("chapter_voice_service")


# ----------------------- env（与 background_scene_analyzer / portrait_demand_analyzer 一致的 fallback 链）-----------------------

CHAPTER_VOICE_ENABLED = os.getenv("CHAPTER_VOICE_ENABLED", "true").lower() == "true"
CHAPTER_VOICE_MODEL = text_llm_model("CHAPTER_VOICE_MODEL", "BG_ANALYZER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
CHAPTER_VOICE_API_KEY = text_llm_api_key("CHAPTER_VOICE_API_KEY", "BG_ANALYZER_API_KEY", "REWRITER_API_KEY")
CHAPTER_VOICE_BASE_URL = text_llm_base_url("CHAPTER_VOICE_BASE_URL", "BG_ANALYZER_BASE_URL", "REWRITER_BASE_URL")
CHAPTER_VOICE_TIMEOUT = float(os.getenv("CHAPTER_VOICE_TIMEOUT", "90.0"))
CHAPTER_VOICE_TEMPERATURE = float(os.getenv("CHAPTER_VOICE_TEMPERATURE", "0.2"))
CHAPTER_VOICE_MAX_TOKENS = int(os.getenv("CHAPTER_VOICE_MAX_TOKENS", "6000"))
CHAPTER_VOICE_RETRIES = int(os.getenv("CHAPTER_VOICE_RETRIES", "1"))

# 单章一次 LLM 切片，正文超过此字符数则截断（防爆 token）。
CHAPTER_VOICE_CONTENT_BUDGET = int(os.getenv("CHAPTER_VOICE_CONTENT_BUDGET", "8000"))

# TTS 并发批大小（参考 image_generation_service batch_size=5）。
CHAPTER_VOICE_BATCH_SIZE = int(os.getenv("CHAPTER_VOICE_BATCH_SIZE", "5"))

# 单章最多切多少条 VoiceLine（防爆；单条 200 字时一章正文 ~3000 字
# 一般切 30-60 条）。
CHAPTER_VOICE_MAX_LINES = int(os.getenv("CHAPTER_VOICE_MAX_LINES", "200"))
CHAPTER_VOICE_SLICE_VERSION = "speaker_context_v2"

# 章节语音切片长度与 Script IR segment / TTS 闸门共用同一来源
# （TTS_MAX_TEXT_LENGTH，minimax/aliyun 默认 600），保证图行、语音行、
# TTS 三处粒度一致；不再单独维护 200 字旧契约。
from app.services.tts_service import TTS_MAX_TEXT_LENGTH  # noqa: E402

CHAPTER_VOICE_MAX_TEXT_CHARS = TTS_MAX_TEXT_LENGTH

# 单条 TTS 临时失败的重试次数（不含第一次正常调用）。
CHAPTER_VOICE_TTS_RETRIES = int(os.getenv("CHAPTER_VOICE_TTS_RETRIES", "2"))
# 重试指数退避：delay = min(BASE * 2 ** attempt, MAX)。
CHAPTER_VOICE_TTS_RETRY_BASE_DELAY = float(os.getenv("CHAPTER_VOICE_TTS_RETRY_BASE_DELAY", "1.0"))
CHAPTER_VOICE_TTS_RETRY_MAX_DELAY = float(os.getenv("CHAPTER_VOICE_TTS_RETRY_MAX_DELAY", "6.0"))

# RPM 限流期间的全局 cooldown。MiniMax base_resp.code=1002 表示账号级每分钟
# 请求数已超限，所有并发协程必须一起等到下个 RPM 窗口（或被外部 cooldown
# 信号唤醒）。1 个 RPM 窗口通常 60s，留 5s 余量。
CHAPTER_VOICE_RPM_COOLDOWN_SECONDS = float(os.getenv("CHAPTER_VOICE_RPM_COOLDOWN_SECONDS", "45.0"))
# RPM 限流命中后的单条重试退避下限（指数退避会从 BASE*2**attempt 起跳，
# 但限流窗口没恢复前重试无意义，所以与 cooldown 取大值）。
CHAPTER_VOICE_RPM_RETRY_DELAY = float(os.getenv("CHAPTER_VOICE_RPM_RETRY_DELAY", "30.0"))

CACHE_DIR = Path(os.getenv(
    "CHAPTER_VOICE_CACHE_DIR",
    str(Path(__file__).parent.parent.parent / ".cache" / "chapter_voice"),
))


# ----------------------- RPM rate-limit cooldown -----------------------
#
# 全局 cooldown：任何协程看到 RPM 限流错误 → 记录 cooldown_until 时间戳，
# 其他协程在发请求前检查该时间戳，未到期则集体 sleep 到期再继续。
# 这样一次限流不会导致 100 个协程各自疯狂重试把下个窗口也打爆。

class _RPMCooldownGate:
    """跨协程共享的 RPM cooldown 状态。

    并发安全依赖 GIL + 单 event loop（asyncio 单线程）。``_cooldown_until``
    是普通 float（绝对单调时间），读写无锁；最坏情况是某协程多等/少等
    一个调度 tick，对限流场景完全可接受。
    """

    __slots__ = ("_cooldown_until",)

    def __init__(self) -> None:
        self._cooldown_until: float = 0.0

    def is_active(self, *, now: Optional[float] = None) -> bool:
        import time as _time
        return (now if now is not None else _time.monotonic()) < self._cooldown_until

    def remaining(self, *, now: Optional[float] = None) -> float:
        import time as _time
        t = now if now is not None else _time.monotonic()
        return max(0.0, self._cooldown_until - t)

    def trigger(self, duration: float, *, now: Optional[float] = None) -> None:
        """登记 cooldown，``duration`` 秒内所有协程都不能发 RPM 请求。"""
        import time as _time
        t = now if now is not None else _time.monotonic()
        new_until = t + max(0.0, duration)
        # 取较大值，避免一个旧 cooldown 被新 cooldown 覆盖缩短
        if new_until > self._cooldown_until:
            self._cooldown_until = new_until


_rpm_cooldown = _RPMCooldownGate()


def _is_rate_limit_error(err_text: Optional[str]) -> bool:
    if not err_text:
        return False
    s = str(err_text).lower()
    return any(tok in s for tok in _RATE_LIMIT_TOKENS)


# ----------------------- dataclass -----------------------

@dataclass
class VoiceLine:
    """单条配音行。

    ``kind="narration"`` 时 ``speaker_name`` / ``character_id`` /
    ``character_voice`` 为空（旁白用默认 narrator 音色）。

    ``text`` 是要送进 TTS 的原文（中文）。``Asset.prompt`` 存这个值，P3 装配时
    按 text 匹配 vngraph dialogue 节点回填 audio_url。

    ``audio_url`` / ``asset_id`` / ``cached`` / ``error`` 在 TTS + 落库阶段
    填充；LLM 切片阶段只填前 6 个字段。
    """
    kind: str  # "narration" | "dialogue"
    text: str
    order_index: int = 0
    speaker_name: Optional[str] = None
    character_id: Optional[str] = None
    character_voice: Optional[str] = None
    emotion: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[str] = None
    audio_url: Optional[str] = None
    asset_id: Optional[int] = None
    cached: Optional[bool] = None
    error: Optional[str] = None

    def is_dialogue(self) -> bool:
        return self.kind == "dialogue"


@dataclass
class VoiceSliceReport:
    """Structured record of how the slice actually completed.

    ``degraded_mode`` is ``None`` on a clean LLM success. Any other value
    means the LLM path failed and we fell back to a regex splitter that
    drops dialogue → the assembler surfaces this on
    ``Meta.generation_report.voice_lines.fallback_used``.
    """

    degraded_mode: Optional[str] = None
    dialogue_count: int = 0
    narration_count: int = 0
    reason: Optional[str] = None

    @classmethod
    def from_lines(cls, lines: List["VoiceLine"], *, degraded_mode: Optional[str] = None, reason: Optional[str] = None) -> "VoiceSliceReport":
        dialogue = sum(1 for ln in lines if ln.is_dialogue())
        narration = max(0, len(lines) - dialogue)
        return cls(
            degraded_mode=degraded_mode,
            dialogue_count=dialogue,
            narration_count=narration,
            reason=reason,
        )


# ----------------------- TTS retry classification -----------------------

_TRANSIENT_TOKENS = (
    "超时", "timeout", "timed out",
    "429", "too many requests", "qps",
    "500", "502", "503", "504",
    # 阿里云 CosyVoice 网关类临时错误（偶发，重试通常能成功）
    "gateway", "service unavailable", "internal error",
    # MiniMax 业务层限流：HTTP 200 但 base_resp.code=1002，message 含
    # "rate limit exceeded(RPM)"。与 429 不同 —— 必须按 token 匹配。
    "rate limit", "rate-limit",
    "rpm", "rps",
    "1002",
)

# RPM / RPS 限流类错误标记 —— 触发后调用方应主动 cooldown，而不是用
# 普通指数退避（窗口通常需要 30s+ 才能恢复，指数退避来不及）。
_RATE_LIMIT_TOKENS = (
    "rate limit", "rate-limit", "rpm", "rps", "1002", "429",
    "too many requests", "qps",
)

# 音色失效类永久错误：原 vcn 在该账号下未授权 / 不存在 —— 原音色重试无意义，
# 由 _fallback_speaker_for_error 切默认音色合成一次。
_VOICE_INVALID_TOKENS = (
    "418", "voice not found", "vcn", "音色",
    "speaker", "no such voice",
)


def _is_transient_tts_error(err_text: str) -> bool:
    """判断 TTS 失败是否值得在原音色上重试（超时 / 429 / 5xx / 网关类）。

    持久性错误（音色不存在、文本不合法、鉴权失败）默认不在这里重试 ——
    音色失效交给 ``_fallback_speaker_for_error`` 切默认音色兜底。"""
    if not err_text:
        return False
    s = str(err_text).lower()
    if any(tok in s for tok in _VOICE_INVALID_TOKENS):
        return False
    return any(tok in s for tok in _TRANSIENT_TOKENS)


def _is_voice_invalid_error(err_text: Optional[str]) -> bool:
    """音色失效错误（CosyVoice 418 / vcn 不存在）→ True。

    与 transient 区分：这种错误重试原音色无意义，必须换音色。"""
    if not err_text:
        return False
    s = str(err_text).lower()
    return any(tok in s for tok in _VOICE_INVALID_TOKENS)


# ----------------------- system prompt -----------------------

SYSTEM_PROMPT = """你是视觉小说的配音导演。给你一章中文小说正文 + 角色卡列表，你的工作是
把正文切成「按原文顺序、可直接送进 TTS」的有序片段列表。

## 输出 JSON 结构
{
  "lines": [
    {
      "kind": "narration | dialogue",
      "text": "原文片段（必须从正文里逐字截取，不能改写不能总结）",
      "speaker_name": "角色名（dialogue 必填，必须来自角色卡；narration 留空字符串）",
      "emotion": "情绪标签，英文小写，例 calm/happy/sad/angry/excited/surprise/fear/neutral"
    }
  ]
}

## 切片规则（必读）
1. **顺序 = 原文顺序**：lines 必须按正文出现顺序排列，不能跳段、不能调位。
2. **kind 判定**：
   - 旁白（描写、心理、动作、环境）→ kind=narration，speaker_name 留空。
   - 角色说出口的话 → kind=dialogue，speaker_name 必须是该句的说话角色。
3. **text 必须原文逐字截取**：不能改写、不能翻译、不能总结、不能加引号。如果一段
   超过 200 字，按语义自然断点拆成多行（不要在词中间断）。
4. **speaker_name 严格从角色卡里挑**：不能编造。如果一句对白分不清谁说的，把它
   归到旁白（kind=narration）而不是瞎猜。
5. **emotion 给 dialogue 必填、给 narration 可留 calm**：根据该句在原文里的情绪
   挑，英文小写。
6. **不写剧情摘要、不写画面描述、不输出原文之外的内容**。

## 输出约束
- 只输出 JSON，不要 markdown 围栏，不要解释。
- lines 至少 1 条；超过 120 条时只保留前 120 条（按原文顺序）。
"""


# ----------------------- service -----------------------

class ChapterVoiceService:
    """P2.1 — 章节级批量配音编排器。

    典型用法（P2.2 router）：
        svc = ChapterVoiceService(db)
        lines = await svc.generate_chapter_voices(project_id, chapter_index)
        # lines: List[VoiceLine]，已合成并落库
    """

    def __init__(self, db: Optional[Session] = None) -> None:
        self._db = db
        self._client = None
        # 最近一次 generate_chapter_voices 的失败统计，供调用方读取。
        # 保留失败明细，让调用方能展示哪些句子没有合成成功。
        self.last_summary: Optional[Dict[str, Any]] = None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # --------- db session（与 portrait_demand_analyzer 同样的懒加载）---------

    @property
    def db(self) -> Session:
        if self._db is None:
            from app.database import SessionLocal
            self._db = SessionLocal()
        return self._db

    # --------- llm client ---------

    @property
    def client(self):
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=CHAPTER_VOICE_API_KEY,
                pool_env=("CHAPTER_VOICE_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=CHAPTER_VOICE_BASE_URL,
            )
        return self._client

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """带重试 + 指数退避 + JSON response_format 的 LLM 调用。

        直接照抄 ``background_scene_analyzer_service._call_llm`` (L154-185)。
        """
        last_err: Optional[Exception] = None
        for attempt in range(CHAPTER_VOICE_RETRIES + 1):
            try:
                resp = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=resolve_request_model(CHAPTER_VOICE_MODEL),
                        temperature=CHAPTER_VOICE_TEMPERATURE,
                        max_tokens=CHAPTER_VOICE_MAX_TOKENS,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    ),
                    timeout=CHAPTER_VOICE_TIMEOUT,
                )
                content = resp.choices[0].message.content or "{}"
                return json.loads(content)
            except asyncio.TimeoutError as e:
                last_err = e
                logger.warning("chapter_voice LLM timeout (attempt %d)", attempt + 1)
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning("chapter_voice LLM json parse failed (attempt %d): %s", attempt + 1, e)
            except Exception as e:
                last_err = e
                logger.warning("chapter_voice LLM call failed (attempt %d): %s", attempt + 1, e)
            if attempt < CHAPTER_VOICE_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(
            f"chapter_voice LLM failed after {CHAPTER_VOICE_RETRIES + 1} attempts: {last_err}"
        )

    # --------- cache（仅缓存 LLM 切片结果，TTS 缓存由 tts_service 自管）---------

    def _cache_key(
        self,
        project_id: int,
        chapter_index: int,
        chapter_content: str,
        characters_block: str,
    ) -> str:
        h = hashlib.sha256()
        h.update(CHAPTER_VOICE_SLICE_VERSION.encode("utf-8"))
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
            logger.warning("chapter_voice cache read failed (%s): %s", path, e)
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
            logger.warning("chapter_voice cache write failed: %s", e)

    # --------- helpers ---------

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

    def _get_story_bible_raw(self, project_id: int) -> Dict[str, Any]:
        """读 StoryBible.raw_json（含 characters 角色卡 voice 字段）。无则空 dict。"""
        from app.models import StoryBible
        row = self.db.query(StoryBible).filter(
            StoryBible.project_id == project_id,
        ).first()
        if not row:
            return {}
        raw = row.raw_json if isinstance(row.raw_json, dict) else {}
        # 顶层 characters 字段优先（部分项目可能只填了 column 没填 raw_json）
        if not raw.get("characters") and row.characters:
            raw = dict(raw)
            raw["characters"] = row.characters
        return raw

    @staticmethod
    def _character_id(character_name: str, project_id: int) -> str:
        """与 ``prompt_builder_service.generate_character_id`` 一致的算法，
        保证下游 ``Asset.character_id`` 列与立绘/关键帧表对齐。"""
        data = f"{character_name}|{project_id}"
        return hashlib.md5(data.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _build_characters_block(story_bible: Dict[str, Any]) -> Tuple[str, Dict[str, Dict[str, Any]]]:
        """从 story_bible 抽角色卡，构造 (给 LLM 看的简短文本块, name→card 查表)。

        ``card`` 至少含 ``voice`` 字段（Story Bible 生成阶段写好的阿里云音色描述）。
        """
        raw_chars = story_bible.get("characters") or []
        cards: Dict[str, Dict[str, Any]] = {}
        lines: List[str] = []
        for c in raw_chars:
            if isinstance(c, str):
                card = {"name": c}
            elif isinstance(c, dict):
                card = c
            else:
                continue
            name = (card.get("name") or card.get("name_cn") or "").strip()
            if not name:
                continue
            cards[name] = card
            voice = card.get("voice") or ""
            gender = card.get("gender") or ""
            age = card.get("age") or ""
            lines.append(
                f"- name={name} | voice={voice} | gender={gender} | age={age}"
            )
        return ("\n".join(lines), cards)

    @staticmethod
    def _trim_content(content: str, budget: int) -> str:
        """按字符预算截断正文，保留头尾。"""
        if len(content) <= budget:
            return content
        half = budget // 2
        head = content[:half]
        tail = content[-(budget - half):]
        return f"{head}\n…（中段省略 {len(content) - budget} 字）…\n{tail}"

    @staticmethod
    def _coerce_text(value: Any, limit: int = CHAPTER_VOICE_MAX_TEXT_CHARS) -> str:
        """规范化 VoiceLine.text：去首尾空白、限长。"""
        if value is None:
            return ""
        return str(value).strip()[:limit]

    @staticmethod
    def _normalize_kind(value: Any) -> str:
        if value is None:
            return "narration"
        s = str(value).strip().lower()
        if s in {"dialogue", "dialog", "speech", "say", "said"}:
            return "dialogue"
        return "narration"

    @staticmethod
    def _normalize_emotion(value: Any) -> Optional[str]:
        if value is None:
            return None
        s = str(value).strip().lower()
        if not s or s in {"none", "null"}:
            return None
        return s

    # --------- prompt assembly ---------

    def _build_user_prompt(
        self,
        project_id: int,
        chapter_index: int,
        chapter_content: str,
        characters_block: str,
    ) -> str:
        trimmed = self._trim_content(chapter_content, CHAPTER_VOICE_CONTENT_BUDGET)
        return f"""<ProjectId>{project_id}</ProjectId>
<ChapterIndex>{chapter_index}</ChapterIndex>

<CharacterCards>
{characters_block or "(no character cards provided — treat all spoken lines as narration)"}
</CharacterCards>

<ChapterText>
{trimmed}
</ChapterText>

<OutputSchemaHint>
返回 JSON: {{"lines": [line, ...]}}
每个 line 字段：
- kind (str, "narration" 或 "dialogue")
- text (str, 原文中文片段, <=200 字, 必须原文逐字截取, 不能改写)
- speaker_name (str, dialogue 时必填且必须来自上面角色卡; narration 时留空字符串)
- emotion (str, 英文小写, 例 calm/happy/sad/angry/excited/surprise/fear/neutral)
</OutputSchemaHint>

只输出严格 JSON，不要 markdown 围栏，不要解释。
"""

    # --------- LLM slice ---------

    async def _slice_chapter(
        self,
        project_id: int,
        chapter_index: int,
        chapter_content: str,
        story_bible: Dict[str, Any],
        script_ir: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[VoiceLine], VoiceSliceReport]:
        """单章一次 LLM 调用 → 规范化、过滤、限频 → ``(List[VoiceLine], VoiceSliceReport)``。

        若传入 ``script_ir``（script-ir-v3，LLM 已切分并经确定性守护校验），
        则直接消费其 paragraphs——语音与图行共用同一事实源，跳过独立 LLM 切片。
        失败 / 关闭 / 缺输入时 fallback 到段落切分（``_fallback_split``），
        并在返回的 ``VoiceSliceReport.degraded_mode`` 中标记降级原因，
        让上层 assembler/router 能把信息写进 ``Meta.generation_report``。
        """
        characters_block, cards = self._build_characters_block(story_bible)

        # 单事实源：Script IR segments 直驱语音行（与 VN 图行同粒度）。
        if script_ir and isinstance(script_ir.get("paragraphs"), list) and script_ir["paragraphs"]:
            lines = self._lines_from_script_ir(script_ir, cards, project_id)
            if lines:
                return lines, VoiceSliceReport.from_lines(lines)
        valid_names = set(cards.keys())

        # cache hit
        key = self._cache_key(project_id, chapter_index, chapter_content, characters_block)
        cached = self._cache_get(key)
        if cached is not None:
            logger.info(
                "chapter_voice cache hit project=%d chapter=%d (%d lines)",
                project_id, chapter_index, len(cached),
            )
            lines = self._dicts_to_lines(cached, project_id, cards)
            return lines, VoiceSliceReport.from_lines(lines)

        # 早退：正文过短 → fallback
        if not chapter_content or len(chapter_content) < 200:
            logger.info(
                "chapter_voice content too short chapter=%d len=%d, fallback to split",
                chapter_index, len(chapter_content or ""),
            )
            lines = self._fallback_split(project_id, chapter_content, cards)
            return lines, VoiceSliceReport.from_lines(
                lines, degraded_mode="content_too_short",
            )

        if not CHAPTER_VOICE_ENABLED or not api_key_available(
            CHAPTER_VOICE_API_KEY,
            pool_env=("CHAPTER_VOICE_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info("chapter_voice disabled (no API key), fallback to split chapter=%d", chapter_index)
            lines = self._fallback_split(project_id, chapter_content, cards)
            return lines, VoiceSliceReport.from_lines(
                lines, degraded_mode="no_api_key",
            )

        user_prompt = self._build_user_prompt(
            project_id, chapter_index, chapter_content, characters_block,
        )
        try:
            data = await asyncio.wait_for(
                self._call_llm(SYSTEM_PROMPT, user_prompt),
                timeout=CHAPTER_VOICE_TIMEOUT * 2,
            )
        except asyncio.TimeoutError:
            logger.warning("chapter_voice LLM timed out, chapter=%d, fallback to split", chapter_index)
            lines = self._fallback_split(project_id, chapter_content, cards)
            return lines, VoiceSliceReport.from_lines(
                lines, degraded_mode="llm_timeout",
            )
        except Exception as e:
            logger.warning("chapter_voice LLM failed chapter=%d: %s, fallback to split", chapter_index, e)
            lines = self._fallback_split(project_id, chapter_content, cards)
            return lines, VoiceSliceReport.from_lines(
                lines, degraded_mode="llm_exception", reason=str(e),
            )

        raw_lines = data.get("lines") if isinstance(data, dict) else None
        if not isinstance(raw_lines, list):
            raw_lines = []

        lines: List[VoiceLine] = []
        seen_texts: set = set()
        for d in raw_lines:
            if not isinstance(d, dict):
                continue
            text = self._coerce_text(d.get("text"))
            if not text:
                continue
            # 去重：完全相同的连续对白合并（防 LLM 失控刷同句）
            if text in seen_texts:
                continue
            kind = self._normalize_kind(d.get("kind"))
            speaker_name = ""
            character_id: Optional[str] = None
            character_voice: Optional[str] = None
            gender: Optional[str] = None
            age: Optional[str] = None
            if kind == "dialogue":
                name = str(d.get("speaker_name") or "").strip()
                if name and (not valid_names or name in valid_names):
                    speaker_name = name
                    character_id = self._character_id(name, project_id)
                    card = cards.get(name, {})
                    character_voice = card.get("voice")
                    gender = card.get("gender")
                    age = card.get("age")
                else:
                    # speaker_name 不在角色卡 → 退回 narration
                    logger.info(
                        "chapter_voice degrade dialogue→narration (unknown speaker %r) chapter=%d",
                        name, chapter_index,
                    )
                    kind = "narration"
            seen_texts.add(text)
            lines.append(VoiceLine(
                kind=kind,
                text=text,
                speaker_name=speaker_name or None,
                character_id=character_id,
                character_voice=character_voice,
                emotion=self._normalize_emotion(d.get("emotion")),
                gender=gender,
                age=age,
            ))
            if len(lines) >= CHAPTER_VOICE_MAX_LINES:
                break

        if not lines:
            # LLM 返回空 → fallback
            logger.warning("chapter_voice LLM produced 0 lines chapter=%d, fallback", chapter_index)
            lines = self._fallback_split(project_id, chapter_content, cards)
            return lines, VoiceSliceReport.from_lines(
                lines, degraded_mode="empty_result",
            )

        # 写缓存（仅 persist 通过校验的，order_index 在外层主入口赋值）
        self._cache_set(key, [asdict(ln) for ln in lines])

        logger.info(
            "chapter_voice slice ok chapter=%d lines=%d (dialogue=%d narration=%d)",
            chapter_index, len(lines),
            sum(1 for ln in lines if ln.is_dialogue()),
            sum(1 for ln in lines if not ln.is_dialogue()),
        )
        return lines, VoiceSliceReport.from_lines(lines)

    def _lines_from_script_ir(
        self,
        script_ir: Dict[str, Any],
        cards: Dict[str, Dict[str, Any]],
        project_id: int,
    ) -> List[VoiceLine]:
        """script-ir-v3 segments → VoiceLine（与 VN 图行同一份切分）。"""
        lines: List[VoiceLine] = []
        for item in script_ir.get("paragraphs") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            kind = self._normalize_kind(item.get("kind"))
            speaker_name = ""
            character_id: Optional[str] = None
            character_voice = gender = age = None
            if kind in ("dialogue", "monologue"):
                speaker_name = str(item.get("speaker_name") or "").strip()
                cid = str(item.get("speaker_character_id") or "").strip()
                if speaker_name and speaker_name in cards:
                    character_id = cid or self._character_id(speaker_name, project_id)
                    card = cards.get(speaker_name, {})
                    character_voice = card.get("voice")
                    gender = card.get("gender")
                    age = card.get("age")
                else:
                    # speaker 不在角色卡 → 退回 narration（与 LLM 切片同规则）
                    logger.info(
                        "script_ir voice degrade dialogue→narration (unknown speaker %r)",
                        speaker_name,
                    )
                    kind = "narration"
                    speaker_name = ""
            lines.append(
                VoiceLine(
                    kind=kind,
                    text=text,
                    speaker_name=speaker_name or None,
                    character_id=character_id,
                    character_voice=character_voice,
                    emotion=self._normalize_emotion(item.get("emotion")),
                    gender=gender,
                    age=age,
                )
            )
            if len(lines) >= CHAPTER_VOICE_MAX_LINES:
                break
        return lines

    def _fallback_split(
        self,
        project_id: int,
        chapter_content: str,
        cards: Dict[str, Dict[str, Any]],
    ) -> List[VoiceLine]:
        """LLM 切分失败的最终兜底：不再调本地 splitter（已废弃）—— 把整章按句号切成 narration。
        对白切分完全交给 LLM 主路径；本 fallback 仅保证「链路不死」。
        """
        if not chapter_content:
            return []

        lines: List[VoiceLine] = []
        seen_texts: set = set()

        for chunk in self._split_long(chapter_content):
            chunk = chunk.strip()
            if not chunk or chunk in seen_texts:
                continue
            seen_texts.add(chunk)
            lines.append(VoiceLine(kind="narration", text=chunk))
            if len(lines) >= CHAPTER_VOICE_MAX_LINES:
                break

        logger.info(
            "chapter_voice fallback split produced %d narration lines (LLM had failed; no dialogue recovery)",
            len(lines),
        )
        return lines

    def _split_long(self, text: str) -> List[str]:
        """超长旁白按句号切到 <=CHAPTER_VOICE_MAX_TEXT_CHARS 的小段。"""
        if len(text) <= CHAPTER_VOICE_MAX_TEXT_CHARS:
            return [text]
        out: List[str] = []
        for sentence in re.split(r"(?<=[。！？!?；;])", text):
            s = sentence.strip()
            if not s:
                continue
            # 单句超长（罕见）硬切
            while len(s) > CHAPTER_VOICE_MAX_TEXT_CHARS:
                out.append(s[:CHAPTER_VOICE_MAX_TEXT_CHARS])
                s = s[CHAPTER_VOICE_MAX_TEXT_CHARS:]
            if s:
                out.append(s)
        return out or [text[:CHAPTER_VOICE_MAX_TEXT_CHARS]]

    def _guess_speaker(
        self,
        paragraph: str,
        cards: Dict[str, Dict[str, Any]],
        project_id: int,
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """段落里第一个出现的角色卡角色名 → 猜为说话人。猜不到返回五元 None。

        Returns: (name, character_id, voice, gender, age)
        """
        if not cards:
            return (None, None, None, None, None)
        earliest: Optional[Tuple[int, str]] = None
        for name in cards:
            idx = paragraph.find(name)
            if idx == -1:
                continue
            if earliest is None or idx < earliest[0]:
                earliest = (idx, name)
        if earliest is None:
            return (None, None, None, None, None)
        name = earliest[1]
        cid = self._character_id(name, project_id)
        card = cards.get(name, {})
        return (name, cid, card.get("voice"), card.get("gender"), card.get("age"))

    @staticmethod
    def _dicts_to_lines(
        payload: List[Dict[str, Any]],
        project_id: int,
        cards: Dict[str, Dict[str, Any]],
    ) -> List[VoiceLine]:
        """把缓存里的 dict list 还原成 VoiceLine list。

        缓存里可能缺 character_id/character_voice（旧版/降级），现算兜底。
        """
        out: List[VoiceLine] = []
        for d in payload:
            if not isinstance(d, dict):
                continue
            kind = ChapterVoiceService._normalize_kind(d.get("kind"))
            text = ChapterVoiceService._coerce_text(d.get("text"))
            if not text:
                continue
            speaker = (d.get("speaker_name") or "").strip() or None
            cid = d.get("character_id")
            voice = d.get("character_voice")
            gender = d.get("gender")
            age = d.get("age")
            if kind == "dialogue" and speaker:
                if not cid:
                    cid = ChapterVoiceService._character_id(speaker, project_id)
                card = cards.get(speaker, {})
                if not voice:
                    voice = card.get("voice")
                if not gender:
                    gender = card.get("gender")
                if not age:
                    age = card.get("age")
            elif kind == "narration":
                speaker = None
                cid = None
                voice = None
                gender = None
                age = None
            out.append(VoiceLine(
                kind=kind,
                text=text,
                speaker_name=speaker,
                character_id=cid,
                character_voice=voice,
                emotion=ChapterVoiceService._normalize_emotion(d.get("emotion")),
                gender=gender,
                age=age,
            ))
        return out

    # --------- TTS + 落库 ---------

    def _find_existing_asset(
        self,
        project_id: int,
        chapter_index: int,
        line: VoiceLine,
    ):
        """同 chapter + character + text 已存在且可用 → 复用 Asset。

        只把 ``status='completed'`` 且 ``image_url`` 非空的 asset 当成可复用；
        failed/pending/空 URL 的旧 asset 不算命中（外层会重新合成并写回，
        ``_persist_voice_asset`` 会原地更新而非新建，避免脏数据阻断重试）。
        """
        from app.models import Asset
        q = self.db.query(Asset).filter(
            Asset.project_id == project_id,
            Asset.chapter_index == chapter_index,
            Asset.asset_type == "voice_line",
            Asset.prompt == line.text,
            Asset.status == "completed",
        )
        if line.character_id:
            q = q.filter(Asset.character_id == line.character_id)
        rows = self._query_all_safe(q)
        for row in rows:
            if row.image_url:
                return row
        return None

    def _find_stale_asset_for_update(
        self,
        project_id: int,
        chapter_index: int,
        line: VoiceLine,
    ):
        """找一条 status != completed 或 image_url 空的旧 voice_line asset，
        重新合成成功后用它原地更新（避免脏数据阻断重试，也不重复建条目）。"""
        from app.models import Asset
        q = self.db.query(Asset).filter(
            Asset.project_id == project_id,
            Asset.chapter_index == chapter_index,
            Asset.asset_type == "voice_line",
            Asset.prompt == line.text,
        )
        if line.character_id:
            q = q.filter(Asset.character_id == line.character_id)
        for row in self._query_all_safe(q):
            if row.status != "completed" or not row.image_url:
                return row
        return None

    @staticmethod
    def _query_all_safe(query) -> list:
        """从 SQLAlchemy Query 取 list；对 MagicMock（单元测试）做兼容，
        当 ``all()`` 未被 stub 时退到 ``first()`` 单元素列表。"""
        try:
            rows = query.all()
        except (AttributeError, TypeError):
            rows = None
        if isinstance(rows, list):
            return rows
        if rows is None:
            # 退到 first()，兼容只 stub 了 .first() 的旧测试
            row = query.first() if hasattr(query, "first") else None
            return [row] if row is not None else []
        # 真实 SQLAlchemy 永远返回 list；走到这里说明是 mock 的非 list 返回，
        # 进一步退到 first() 以兼容只 stub .first() 的单元测试。
        row = query.first() if hasattr(query, "first") else None
        return [row] if row is not None else []

    async def _synthesize_line(
        self,
        project_id: int,
        chapter_index: int,
        line: VoiceLine,
    ) -> VoiceLine:
        """单条 VoiceLine → 命中已有 completed Asset 直接复用，否则调
        ``tts_service.synthesize`` 合成 + 落库。

        单条临时失败（超时 / 429 / 5xx）按指数退避重试
        ``CHAPTER_VOICE_TTS_RETRIES`` 次；最终失败保留 ``line.error``，
        不阻断整章任务；失败明细保留在 VoiceLine 结果中供调用方处理。
        """
        existing = self._find_existing_asset(project_id, chapter_index, line)
        if existing and existing.image_url:
            line.audio_url = existing.image_url
            line.asset_id = existing.id
            line.cached = True
            return line

        # 懒导入，避免模块 import 时强依赖 tts_service 初始化（test 场景可能没装 aiohttp 等）
        from app.services.tts_service import tts_service

        # 对白：先查项目级绑定（同角色跨章节永远复用同一 vcn+参数），
        # 再用绑定结果显式覆盖 synthesize 入参，绕过 tts_service 内部 matcher。
        # 旁白：line.character_id 为空，走 tts_service 默认 speaker，不绑定。
        binding = None
        if line.character_id:
            try:
                from app.services.voice_binding_service import VoiceBindingService
                binding = VoiceBindingService(self.db).get_or_allocate(
                    project_id=project_id,
                    character_id=line.character_id,
                    character_name=line.speaker_name,
                    gender=line.gender,
                    age=line.age,
                    character_voice=line.character_voice,
                    emotion=line.emotion,
                )
            except Exception as e:
                # 绑定失败不应阻塞合成：降级到原 matcher 路径
                logger.warning(
                    "chapter_voice voice_binding failed project=%d character=%s err=%s, "
                    "fallback to default matching",
                    project_id, (line.speaker_name or "?"), e,
                )

        # ===== 重试循环（仅主供应商，allow_fallback=False 避免每次重试触发 MiniMax 双计费）=====
        audio_url: Optional[str] = None
        last_err: Optional[str] = None
        # primary_engine 用 result.get("engine") 兜底，记录实际跑的引擎。
        primary_engine_used: Optional[str] = None
        for attempt in range(CHAPTER_VOICE_TTS_RETRIES + 1):
            # RPM cooldown gate：上次任何协程命中限流时，全体排队等到窗口恢复
            if _rpm_cooldown.is_active():
                wait_s = _rpm_cooldown.remaining()
                if wait_s > 0:
                    logger.info(
                        "chapter_voice tts rpm cooldown project=%d chapter=%d order=%d wait=%.1fs",
                        project_id, chapter_index, line.order_index, wait_s,
                    )
                    await asyncio.sleep(wait_s)
            try:
                if binding:
                    result = await tts_service.synthesize(
                        text=line.text,
                        character_name=line.speaker_name,
                        speaker=binding.vcn,
                        emotion_prompt=binding.emotion,
                        speed=binding.speed,
                        pitch=binding.pitch,
                        volume=binding.volume,
                        allow_fallback=False,
                        # MiniMax 是主引擎时也必须使用项目级独占槽；此前这些字段
                        # 只在“其他供应商 → MiniMax fallback”时传递，导致主链路
                        # 所有角色退回同一个默认音色。
                        minimax_voice_id=binding.minimax_voice_id,
                        minimax_speed=binding.minimax_speed,
                        minimax_pitch=binding.minimax_pitch,
                        minimax_volume=binding.minimax_volume,
                    )
                else:
                    result = await tts_service.synthesize(
                        text=line.text,
                        character_name=line.speaker_name,
                        character_voice=line.character_voice,
                        emotion=line.emotion,
                        allow_fallback=False,
                    )
            except asyncio.TimeoutError as e:
                last_err = "tts timeout"
                if attempt < CHAPTER_VOICE_TTS_RETRIES:
                    logger.warning(
                        "chapter_voice tts retry project=%d chapter=%d attempt=%d text=%r err=%s",
                        project_id, chapter_index, attempt + 1, line.text[:40], last_err,
                    )
                    await asyncio.sleep(self._retry_delay_for_error(attempt, last_err))
                    continue
                logger.warning(
                    "chapter_voice tts failed after retries project=%d chapter=%d order=%d text=%r err=%s",
                    project_id, chapter_index, line.order_index, line.text[:80], last_err,
                )
                line.error = f"tts exception: {e}"
                return line
            except Exception as e:
                last_err = str(e)
                if _is_rate_limit_error(last_err):
                    _rpm_cooldown.trigger(CHAPTER_VOICE_RPM_COOLDOWN_SECONDS)
                if attempt < CHAPTER_VOICE_TTS_RETRIES and _is_transient_tts_error(last_err):
                    logger.warning(
                        "chapter_voice tts retry project=%d chapter=%d attempt=%d text=%r err=%s",
                        project_id, chapter_index, attempt + 1, line.text[:40], last_err,
                    )
                    await asyncio.sleep(self._retry_delay_for_error(attempt, last_err))
                    continue
                logger.warning(
                    "chapter_voice tts exception project=%d chapter=%d text=%r err=%s",
                    project_id, chapter_index, line.text[:40], e,
                )
                line.error = f"tts exception: {e}"
                return line

            if not result.get("success"):
                err_msg = result.get("error") or "tts failed"
                last_err = err_msg
                primary_engine_used = result.get("engine") or primary_engine_used
                if _is_rate_limit_error(err_msg):
                    _rpm_cooldown.trigger(CHAPTER_VOICE_RPM_COOLDOWN_SECONDS)
                if attempt < CHAPTER_VOICE_TTS_RETRIES and _is_transient_tts_error(err_msg):
                    logger.warning(
                        "chapter_voice tts retry project=%d chapter=%d attempt=%d text=%r err=%s",
                        project_id, chapter_index, attempt + 1, line.text[:40], err_msg,
                    )
                    await asyncio.sleep(self._retry_delay_for_error(attempt, err_msg))
                    continue
                line.error = err_msg
                logger.warning(
                    "chapter_voice tts failed after retries project=%d chapter=%d order=%d text=%r err=%s",
                    project_id, chapter_index, line.order_index, line.text[:80], err_msg,
                )
                # 跳出循环进入兜底层，而不是直接 return，让 MiniMax fallback 有机会接管
                break

            audio_url = result.get("audio_url")
            primary_engine_used = result.get("engine") or primary_engine_used
            if audio_url:
                break
            # 无 audio_url 但 success=True 视为非临时错误，直接放弃主路径
            last_err = result.get("error") or "tts returned no audio_url"
            line.error = "tts returned no audio_url"
            break

        if not audio_url:
            # ===== 兜底层 1：MiniMax 引擎 fallback（单次，allow_fallback=False 杜绝回弹）=====
            # 主供应商重试耗尽后，强制切到 minimax 引擎合成一次。
            # 失败时把两层错误合并写入 line.error，再尝试 voice-fallback（切默认音色）。
            primary_err_for_merge = last_err
            # 尊重 tts_service 侧 fallback 全局开关与错误分类：若关闭（默认）或错误
            # 类型不在允许兜底范围内，跳过 minimax 直接走 voice-fallback / 返回错误。
            # import 失败视为「不允许 fallback」（保守，避免 dev 环境意外计费）。
            try:
                from app.services.tts_service import _should_use_minimax_fallback as _dispatcher_allows_minimax
                _try_minimax = bool(_dispatcher_allows_minimax(primary_err_for_merge))
            except Exception:
                _try_minimax = False
            if _try_minimax:
                logger.info(
                    "chapter_voice tts minimax fallback project=%d chapter=%d order=%d primary_engine=%s err=%s",
                    project_id, chapter_index, line.order_index,
                    primary_engine_used or "?", (primary_err_for_merge or "")[:200],
                )
                try:
                    mm_result = await tts_service.synthesize(
                        text=line.text,
                        character_name=line.speaker_name,
                        character_voice=line.character_voice,
                        gender=line.gender,
                        age=line.age,
                        emotion=line.emotion,
                        speaker=(binding.vcn if binding else None),
                        emotion_prompt=(binding.emotion if binding else None),
                        speed=(binding.speed if binding else None),
                        pitch=(binding.pitch if binding else None),
                        volume=(binding.volume if binding else None),
                        allow_fallback=False,
                        force_engine="minimax",
                        # per-character minimax 独占槽（避免多角色撞同一个 voice_id）
                        minimax_voice_id=(binding.minimax_voice_id if binding else None),
                        minimax_speed=(binding.minimax_speed if binding else None),
                        minimax_pitch=(binding.minimax_pitch if binding else None),
                        minimax_volume=(binding.minimax_volume if binding else None),
                    )
                except Exception as e:
                    mm_result = {"success": False, "error": f"minimax exception: {e}"}

                if mm_result.get("success") and mm_result.get("audio_url"):
                    logger.info(
                        "chapter_voice tts minimax fallback ok project=%d chapter=%d order=%d",
                        project_id, chapter_index, line.order_index,
                    )
                    # 主路径失败时已写过 line.error，恢复成 None 避免下游误判
                    line.error = None
                    return self._persist_voice_asset(
                        project_id, chapter_index, line, mm_result["audio_url"],
                        cached=bool(mm_result.get("cached")),
                    )
                # minimax 失败：合并错误，继续走 voice-fallback
                mm_err = mm_result.get("error") or "minimax fallback failed"
                last_err = (
                    f"primary=({primary_engine_used or 'unknown'}) {primary_err_for_merge}; "
                    f"fallback=(minimax) {mm_err}"
                )
                line.error = last_err
                logger.warning(
                    "chapter_voice tts minimax fallback failed project=%d chapter=%d order=%d err=%s",
                    project_id, chapter_index, line.order_index, (mm_err or "")[:200],
                )

            # ===== 兜底层 2：音色失效 → 切默认音色（仍走主供应商引擎）=====
            # voice_invalid 是「这个 vcn 在该账号下未授权/不存在」的永久错误，
            # 原音色重试无意义；切到默认旁白音色（aliyun → longshuo_v3）再合成一次。
            # 注意：若主引擎整体超时/429/5xx（即上一轮 minimax 已尝试过），这里通常
            # 也救不回，但代价低（一次 synthesize），保留兜底语义不丢。
            fallback_speaker = self._fallback_speaker_for_error(primary_err_for_merge)
            used_speaker = (binding.vcn if binding else None) or line.character_voice
            if fallback_speaker and fallback_speaker != used_speaker:
                logger.warning(
                    "chapter_voice tts voice fallback project=%d chapter=%d order=%d from=%s to=%s err=%s",
                    project_id, chapter_index, line.order_index,
                    used_speaker or "?", fallback_speaker, primary_err_for_merge,
                )
                try:
                    result = await tts_service.synthesize(
                        text=line.text,
                        character_name=line.speaker_name,
                        speaker=fallback_speaker,
                        emotion_prompt=line.emotion or "calm",
                        allow_fallback=False,
                    )
                except Exception as e:
                    line.error = f"{last_err}; voice_fallback exception: {e}"
                    return line
                if result.get("success") and result.get("audio_url"):
                    audio_url = result["audio_url"]
                    last_err = None
                    line.error = None
                else:
                    vf_err = result.get("error") or "tts voice fallback failed"
                    line.error = f"{last_err}; voice_fallback={vf_err}"
                    return line
            else:
                # 没有可切的默认音色（错误不是 voice_invalid 类），直接返回合并错误
                line.error = last_err or "tts failed"
                return line

        # 落库：优先复用 stale asset（failed/pending/空 URL），否则新建
        return self._persist_voice_asset(
            project_id, chapter_index, line, audio_url,
            cached=bool(result.get("cached")),
        )

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(
            CHAPTER_VOICE_TTS_RETRY_BASE_DELAY * (2 ** attempt),
            CHAPTER_VOICE_TTS_RETRY_MAX_DELAY,
        )

    @staticmethod
    def _retry_delay_for_error(attempt: int, err_text: Optional[str]) -> float:
        """带错误类型的重试退避。

        普通瞬时错误走指数退避；限流类错误强制至少 ``CHAPTER_VOICE_RPM_RETRY_DELAY``
        秒，给上游账号级 RPM 窗口足够时间恢复（指数退避的 1s/2s/4s 在 RPM
        场景下毫无意义，反而会立刻把下个窗口也打爆）。
        """
        if _is_rate_limit_error(err_text):
            return max(
                CHAPTER_VOICE_RPM_RETRY_DELAY,
                ChapterVoiceService._retry_delay(attempt),
            )
        return ChapterVoiceService._retry_delay(attempt)

    @staticmethod
    def _fallback_speaker_for_error(err_text: Optional[str]) -> Optional[str]:
        """音色失效错误（CosyVoice 418 / voice-not-found 类）→ 返回默认旁白音色。

        CosyVoice 418 在 aliyun 控制台对应「vcn 不存在或未对该 AccessKey 授权」，
        任何文本都会失败，原音色重试无意义。切到引擎默认旁白音色兜底合成，
        保证用户听到完整章节，事后可以由 binding 调整替换。

        其他错误（超时、429、5xx、文本不合法）不触发本 fallback —— 它们要么
        本身重试有效，要么换了音色也没用。
        """
        if not _is_voice_invalid_error(err_text):
            return None
        try:
            from app.services.tts_service import TTS_DEFAULT_SPEAKER
            return TTS_DEFAULT_SPEAKER
        except Exception:
            return None

    def _persist_voice_asset(
        self,
        project_id: int,
        chapter_index: int,
        line: VoiceLine,
        audio_url: str,
        cached: bool = False,
    ) -> VoiceLine:
        """把合成好的 audio_url 落库；优先更新 stale asset，否则新建。

        只有拿到 audio_url 才会调到这里 → 重试不会产生重复条目。
        """
        from app.models import Asset

        stale = self._find_stale_asset_for_update(project_id, chapter_index, line)
        try:
            if stale is not None:
                stale.image_url = audio_url
                stale.status = "completed"
                stale.target_name = line.speaker_name or "narration"
                stale.character_id = line.character_id
                stale.emotion = line.emotion
                stale.processed = 1
                self.db.commit()
                self.db.refresh(stale)
                asset = stale
            else:
                asset = Asset(
                    project_id=project_id,
                    chapter_index=chapter_index,
                    asset_type="voice_line",
                    target_name=line.speaker_name or "narration",
                    prompt=line.text,
                    image_url=audio_url,
                    character_id=line.character_id,
                    emotion=line.emotion,
                    status="completed",
                    processed=1,
                )
                self.db.add(asset)
                self.db.commit()
                self.db.refresh(asset)
        except Exception as e:
            self.db.rollback()
            logger.error(
                "chapter_voice asset persist failed project=%d chapter=%d err=%s",
                project_id, chapter_index, e,
            )
            line.error = f"persist failed: {e}"
            # 音频已合成，URL 仍返回，前端可临时播放
            line.audio_url = audio_url
            return line

        line.audio_url = audio_url
        line.asset_id = asset.id
        line.cached = cached
        return line

    async def _synthesize_batch_concurrent(
        self,
        project_id: int,
        chapter_index: int,
        lines: List[VoiceLine],
    ) -> None:
        """并发合成 + 串行落库保序。

        参考 ``image_generation_service`` batch_size 切批 + ``asyncio.gather``
        的并发模式（learn note ``image_generation_service.py_learn.md``）。
        tts_service 内部已有 ``Semaphore(2)`` 兜底，这里再按 batch_size 分批
        限制内存与失败爆炸半径。
        """
        for i in range(0, len(lines), CHAPTER_VOICE_BATCH_SIZE):
            batch = lines[i:i + CHAPTER_VOICE_BATCH_SIZE]
            await asyncio.gather(*[
                self._synthesize_line(project_id, chapter_index, ln)
                for ln in batch
            ], return_exceptions=True)

    @staticmethod
    def _build_voice_summary(lines: List["VoiceLine"]) -> Dict[str, Any]:
        """从 generate_chapter_voices 的产出汇总失败详情，供调用方写进
        voice generation summary。

        ``failed_items`` 每条含 order_index / kind / speaker / text_preview /
        error，方便前端与日志定位具体失败句子。
        """
        total = len(lines)
        succeeded = sum(1 for ln in lines if ln.audio_url)
        failed_items: List[Dict[str, Any]] = []
        for ln in lines:
            if ln.audio_url:
                continue
            failed_items.append({
                "order_index": ln.order_index,
                "kind": ln.kind or "narration",
                "speaker": ln.speaker_name or ("旁白" if ln.kind != "dialogue" else "?"),
                "text_preview": (ln.text or "")[:80],
                "error": ln.error or "unknown tts failure",
            })
        return {
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "failed_items": failed_items,
        }

    # --------- main entry ---------

    async def generate_chapter_voices(
        self,
        project_id: int,
        chapter_index: int,
    ) -> List[VoiceLine]:
        """一章正文 → 切片 → 并发 TTS → 落库 → 返回有序 List[VoiceLine]。

        步骤（blueprint P2.1）：
        1. 读 ``ChapterContent.content`` + ``StoryBible`` 角色卡。
        2. LLM 切有序 ``VoiceLine``。
        3. 并发调 ``tts_service.synthesize``（batch_size + Semaphore 双重限流）。
        4. 落库 ``Asset(asset_type="voice_line", chapter_index, character_id,
           emotion, prompt=text, image_url=audio_url)``。

        Args:
            project_id: 项目 ID
            chapter_index: 章节序号

        Returns:
            List[VoiceLine]，按正文顺序；每条带 audio_url / asset_id（成功）
            或 error（失败）。章节不存在时返回空列表。

        Raises:
            AppError: TTS 功能关闭时返回稳定的 ``tts.feature_disabled`` 语义。
        """
        from app.services.tts_service import TTS_ENABLED

        start = time.time()

        if not TTS_ENABLED:
            logger.warning(
                "chapter_voice reject project=%d chapter=%d (TTS disabled)",
                project_id, chapter_index,
            )
            raise AppError(
                code="tts.feature_disabled",
                message="TTS 功能未启用",
                status_code=503,
            )

        chapter_content = self._get_chapter_content(project_id, chapter_index)
        if not chapter_content:
            logger.warning(
                "chapter_voice skip project=%d chapter=%d (no chapter content)",
                project_id, chapter_index,
            )
            return []

        story_bible = self._get_story_bible_raw(project_id)

        # 步骤 ②：LLM 切片（含 fallback）
        lines, slice_report = await self._slice_chapter(
            project_id, chapter_index, chapter_content, story_bible,
        )
        if not lines:
            logger.warning(
                "chapter_voice produced 0 lines project=%d chapter=%d degraded_mode=%s",
                project_id, chapter_index, slice_report.degraded_mode,
            )
            return []

        # 赋 order_index（落库顺序 = 正文顺序，前端播放器按这个排）
        for idx, ln in enumerate(lines):
            ln.order_index = idx

        # 步骤 ③④：并发 TTS + 串行落库
        await self._synthesize_batch_concurrent(
            project_id, chapter_index, lines,
        )

        succeeded = sum(1 for ln in lines if ln.audio_url)
        failed = sum(1 for ln in lines if not ln.audio_url)
        logger.info(
            "chapter_voice done project=%d chapter=%d total=%d succeeded=%d failed=%d degraded_mode=%s elapsed=%.2fs",
            project_id, chapter_index, len(lines), succeeded, failed,
            slice_report.degraded_mode, time.time() - start,
        )

        # 汇总失败详情写到 self.last_summary，供 assembler/router 转发；
        # 同时按行 warn 日志，方便定位每一条失败句子。
        summary = self._build_voice_summary(lines)
        # 把切片降级信息一并塞进 summary，让 assembler 能写进 generation_report
        summary["slice_degraded_mode"] = slice_report.degraded_mode
        summary["slice_dialogue_count"] = slice_report.dialogue_count
        summary["slice_narration_count"] = slice_report.narration_count
        self.last_summary = summary
        for item in summary["failed_items"]:
            logger.warning(
                "chapter_voice line failed project=%d chapter=%d order=%d kind=%s speaker=%s text=%r err=%s",
                project_id, chapter_index,
                item["order_index"], item["kind"], item["speaker"],
                item["text_preview"], item["error"],
            )
        return lines

    async def synthesize_for_graph_lines(
        self,
        project_id: int,
        chapter_index: int,
        graph_lines: List[Dict[str, Any]],
    ) -> List[VoiceLine]:
        """以 vngraph 的 line 列表为权威来源，直接合成缺失音频 + 落库。

        解决 ``vngraph_generator`` 与本服务 ``_slice_chapter`` 两个独立 LLM
        切片结果不一致的「补齐死循环」：vngraph 切出 78 条 line，本服务只
        切出 73 条 → 缺失 5 条文本永远没有对应 Asset → 补齐反复触发但永远
        生成不出来。本方法**跳过 LLM 切片**，直接拿 graph 里的 line.text
        驱动 TTS，保证 graph 中每一条 line 都能拿到音频。

        Args:
            graph_lines: 从 vngraph 中提取的 line dict 列表，每条至少含
                ``text``；可选 ``speaker_name`` / ``character_id`` / ``emotion``。
                调用方应已过滤掉有 audio_url 的 line（仅传缺失的）。

        Returns:
            List[VoiceLine]，每条带 audio_url（成功）或 error（失败）。
        """
        from app.services.tts_service import TTS_ENABLED

        if not graph_lines:
            return []
        if not TTS_ENABLED:
            raise AppError(
                code="tts.feature_disabled",
                message="TTS 功能未启用",
                status_code=503,
            )

        start = time.time()
        voice_lines: List[VoiceLine] = []
        for idx, g in enumerate(graph_lines):
            text = (g.get("text") or "").strip()
            if not text:
                continue
            voice_lines.append(VoiceLine(
                kind="dialogue" if g.get("speaker_name") else "narration",
                text=text,
                order_index=idx,
                speaker_name=g.get("speaker_name") or None,
                character_id=g.get("character_id") or None,
                emotion=g.get("emotion") or None,
            ))

        if not voice_lines:
            return []

        await self._synthesize_batch_concurrent(
            project_id, chapter_index, voice_lines,
        )

        succeeded = sum(1 for ln in voice_lines if ln.audio_url)
        failed = len(voice_lines) - succeeded
        logger.info(
            "chapter_voice graph-driven done project=%d chapter=%d total=%d succeeded=%d failed=%d elapsed=%.2fs",
            project_id, chapter_index, len(voice_lines), succeeded, failed,
            time.time() - start,
        )
        return voice_lines


# ----------------------- module-level singleton（轻量调用方直接 import 用）-----------------------

_default_service: Optional[ChapterVoiceService] = None


def get_default_service() -> ChapterVoiceService:
    global _default_service
    if _default_service is None:
        _default_service = ChapterVoiceService()
    return _default_service

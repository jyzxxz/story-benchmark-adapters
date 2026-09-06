"""
AI 味/套路化用语检测 LLM 评审器。

与 quality_check.py 平级：
  - quality_check 过**硬规则**（字数 / 标题 / 角色名 / ending_hook）。
  - 本模块过 **LLM 味道**（套话词表 / 模板句 / 排比灌水 / 抽象情绪外露）。

调用形态参考 ``background_scene_analyzer_service.py`` 的 ``_call_llm``：
  - ``response_format={"type": "json_object"}`` 强制 JSON 输出
  - ``asyncio.wait_for`` 外层超时兜底
  - 指数退避重试 (``0.5 * 2**attempt``)
  - sha256 缓存 (text + style_rules) → ``backend/.cache/ai_flavor/<hash>.json``

公开入口：``await ai_flavor_check.check(text, style_rules) -> AiFlavorReport``
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.services.api_key_pool import PooledAsyncOpenAI
from app.services.text_llm_config import DEFAULT_TEXT_LLM_MODEL, resolve_request_model, text_llm_api_key, text_llm_base_url, text_llm_model

logger = logging.getLogger("agent.ai_flavor_check")

# ----------------------- env（与 background_scene_analyzer 一致的 fallback 链）-----------------------

AI_FLAVOR_MODEL = text_llm_model("AI_FLAVOR_MODEL", "BG_ANALYZER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
AI_FLAVOR_API_KEY = text_llm_api_key("AI_FLAVOR_API_KEY", "BG_ANALYZER_API_KEY")
AI_FLAVOR_BASE_URL = text_llm_base_url("AI_FLAVOR_BASE_URL", "BG_ANALYZER_BASE_URL")
AI_FLAVOR_TIMEOUT = float(os.getenv("AI_FLAVOR_TIMEOUT", "90.0"))
AI_FLAVOR_TEMPERATURE = float(os.getenv("AI_FLAVOR_TEMPERATURE", "0.2"))
AI_FLAVOR_MAX_TOKENS = int(os.getenv("AI_FLAVOR_MAX_TOKENS", "4000"))
AI_FLAVOR_RETRIES = int(os.getenv("AI_FLAVOR_RETRIES", "1"))

CACHE_DIR = Path(os.getenv(
    "AI_FLAVOR_CACHE_DIR",
    str(Path(__file__).parent.parent.parent / ".cache" / "ai_flavor"),
))

# P4.2 在 presets.py 加 AI_FLAVOR_MIN_SCORE / AI_FLAVOR_MAX_RETRIES。
# 为防止 DAG 顺序里 P4.1 先于 P4.2 集成、presets 里暂缺常量，这里加安全 fallback。
try:
    from app.agent.presets import AI_FLAVOR_MIN_SCORE  # type: ignore
except ImportError:  # pragma: no cover - 临时兜底，P4.2 落地后会被真实常量覆盖
    AI_FLAVOR_MIN_SCORE = 70
try:
    from app.agent.presets import AI_FLAVOR_MAX_RETRIES  # type: ignore
except ImportError:  # pragma: no cover
    AI_FLAVOR_MAX_RETRIES = 2


# ----------------------- dataclass -----------------------

@dataclass
class AiFlavorReport:
    """AI 味评审结果。

    - ``passed``: ``score >= AI_FLAVOR_MIN_SCORE``
    - ``score``: 0-100，100 = 完全像人写
    - ``issues``: 命中的问题类型字符串，例 ``["套话:不禁", "排比灌水"]``
    - ``rewrite_hint``: 给重写 prompt 的具体英文指令，必须引用正文里具体词/句
    """
    passed: bool
    score: int
    issues: list = field(default_factory=list)
    rewrite_hint: str = ""


# ----------------------- system prompt -----------------------

SYSTEM_PROMPT = """你是中文小说 AI 味检测专家。给一段中文小说正文，判断它有多大"AI 写出来的味道"，输出 JSON:

{
  "score": 0-100 的整数,  100 = 完全像人写, 0 = 极度 AI 味
  "issues": [命中的问题类型字符串,见下],
  "rewrite_hint": "给重写者的具体指令,英文,2-3 句,引用具体词或句"
}

## 命中类型(issues 取值)
- 套话:<具体词>     例: "套话:不禁"、"套话:仿佛"、"套话:一抹"
- 模板句:<片段>     例: "模板句:他知道这一刻"
- 排比灌水          连续 3+ 排比/对仗堆叠
- 抽象情绪外露      "心中涌起一股..."/"莫名的..."这类直接说情绪而非用动作/对白展示
- 形容词堆叠        单句 3+ 形容词修饰同一名词
- 重复用词          同一段里同一个形容词/动作动词出现 3+ 次
- 转折滥用          "然而/但是/却" 高密度

## 中文 AI 味典型套话词表(必查,非穷举)
不禁、仿佛、宛如、一抹、流转、似乎在诉说、莫名的、淡淡的、一缕、微微的、
悄然、霎时、须臾、霎时间、油然而生、心头一紧、目光流转、嘴角上扬、眉眼弯弯、
不约而同、心照不宣、欲言又止、百感交集、五味杂陈

## 模板句(必查,匹配近似变体)
- "他知道/明白,这一刻..."
- "时间/空气 仿佛 静止/凝固"
- "心中/心头 涌起 一股 ..."
- "不是 A,而是 B" 反复使用
- "如果说 A,那么 B"

## 评分基准
- 90-100: 几乎无 AI 味,语言有个人辨识度
- 70-89:  轻微套话但不影响阅读
- 50-69:  明显 AI 味,多处套话或模板句
- 0-49:   重度 AI 味,密集套路化

## 输出约束
- issues 命中时至少列 1 条,没命中给空数组,score 给 90+
- rewrite_hint 必须引用正文里具体的词或句子片段,不能泛泛说"避免套话"
- 只输出 JSON,不要其他文本
"""


# ----------------------- llm client（复用 background_scene_analyzer_service 的模式）-----------------------

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = PooledAsyncOpenAI(
            api_key=AI_FLAVOR_API_KEY,
            pool_env=("AI_FLAVOR_API_KEYS", "BG_ANALYZER_API_KEYS", "OPENAI_API_KEYS"),
            allow_byok=True,
            base_url=AI_FLAVOR_BASE_URL,
        )
    return _client


async def _call_llm(client, system_prompt: str, user_prompt: str) -> dict:
    """带重试 + 指数退避 + JSON response_format 的 LLM 调用。

    直接照抄 ``background_scene_analyzer_service._call_llm`` (L154-185)。
    """
    last_err: Optional[Exception] = None
    for attempt in range(AI_FLAVOR_RETRIES + 1):
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=resolve_request_model(AI_FLAVOR_MODEL),
                    temperature=AI_FLAVOR_TEMPERATURE,
                    max_tokens=AI_FLAVOR_MAX_TOKENS,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                ),
                timeout=AI_FLAVOR_TIMEOUT,
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except asyncio.TimeoutError as e:
            last_err = e
            logger.warning("ai_flavor LLM timeout (attempt %d)", attempt + 1)
        except json.JSONDecodeError as e:
            last_err = e
            logger.warning("ai_flavor LLM json parse failed (attempt %d): %s", attempt + 1, e)
        except Exception as e:
            last_err = e
            logger.warning("ai_flavor LLM call failed (attempt %d): %s", attempt + 1, e)
        if attempt < AI_FLAVOR_RETRIES:
            await asyncio.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"ai_flavor LLM failed after {AI_FLAVOR_RETRIES + 1} attempts: {last_err}")


# ----------------------- 缓存（sha256(text + style_rules)）-----------------------

def _cache_hash(text: str, style_rules: str) -> str:
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    h.update((style_rules or "").encode("utf-8"))
    return h.hexdigest()


def _cache_get(text: str, style_rules: str) -> Optional[dict]:
    p = CACHE_DIR / f"{_cache_hash(text, style_rules)}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("ai_flavor cache read failed (%s): %s", p, e)
            return None
    return None


def _cache_set(text: str, style_rules: str, data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = CACHE_DIR / f"{_cache_hash(text, style_rules)}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("ai_flavor cache write failed: %s", e)


# ----------------------- 公开入口 -----------------------

async def check(text: str, style_rules: str = "") -> AiFlavorReport:
    """对一段章节正文做 AI 味评审。

    Args:
        text: 章节正文。空或过短直接判 passed=True。
        style_rules: Story Bible 里写给 LLM 的风格约束（可空）。

    Returns:
        AiFlavorReport
    """
    if not text or len(text) < 50:
        return AiFlavorReport(passed=True, score=100, issues=[], rewrite_hint="")

    cached = _cache_get(text, style_rules)
    if cached is not None:
        score = int(cached.get("score", 0))
        return AiFlavorReport(
            passed=score >= AI_FLAVOR_MIN_SCORE,
            score=score,
            issues=list(cached.get("issues", [])),
            rewrite_hint=str(cached.get("rewrite_hint", "")),
        )

    user_prompt = (
        f"Style rules from Story Bible:\n{style_rules or '(none)'}\n\n"
        f"Chapter text to review:\n{text}"
    )
    try:
        result = await _call_llm(_get_client(), SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        # LLM 失败时不能让整章死掉：降级为「软通过」，由 caller 决定是否重试。
        logger.warning("ai_flavor check failed, soft-pass: %s", e)
        return AiFlavorReport(passed=True, score=AI_FLAVOR_MIN_SCORE, issues=[],
                              rewrite_hint=f"(ai_flavor check unavailable: {e})")

    try:
        score = int(result.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    issues = [str(x) for x in result.get("issues", []) if x is not None]
    hint = str(result.get("rewrite_hint", ""))

    _cache_set(text, style_rules, {"score": score, "issues": issues, "rewrite_hint": hint})

    return AiFlavorReport(
        passed=score >= AI_FLAVOR_MIN_SCORE,
        score=score,
        issues=issues,
        rewrite_hint=hint,
    )

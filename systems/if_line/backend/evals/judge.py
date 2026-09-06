"""LLM-as-judge: 对 agent 产出的文本按 rubric 打分。

设计要点(防止评分虚高):
- rubric 用"挑毛病优先 + 明确 1-5 锚点 + 强制引用原文"的严格措辞;
- 不要求 LLM 直接输出 0-100 总分(它倾向给高分), 而是让 LLM 逐维度打 1-5,
  由代码从维度均值推导总分, 并对"任一维度 ≤2"强封顶;
- json_object + 重试退避 + sha256 缓存 + 软失败(judge 挂掉软通过)。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from app.services.api_key_pool import PooledAsyncOpenAI
from app.services.text_llm_config import (
    DEFAULT_TEXT_LLM_MODEL,
    resolve_request_model,
    text_llm_api_key,
    text_llm_base_url,
    text_llm_model,
)

from evals.types import JudgeOutcome


JUDGE_MODEL = text_llm_model("EVAL_JUDGE_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
JUDGE_API_KEY = text_llm_api_key("EVAL_JUDGE_API_KEY")
JUDGE_BASE_URL = text_llm_base_url("EVAL_JUDGE_BASE_URL")
JUDGE_TIMEOUT = float(os.getenv("EVAL_JUDGE_TIMEOUT", "90.0"))
JUDGE_RETRIES = int(os.getenv("EVAL_JUDGE_RETRIES", "1"))
JUDGE_TEMPERATURE = float(os.getenv("EVAL_JUDGE_TEMPERATURE", "0.2"))
JUDGE_MAX_TOKENS = int(os.getenv("EVAL_JUDGE_MAX_TOKENS", "2000"))
JUDGE_MIN_SCORE = int(os.getenv("EVAL_JUDGE_MIN_SCORE", "70"))

CACHE_DIR = Path(
    os.getenv(
        "EVAL_JUDGE_CACHE_DIR",
        str(Path(__file__).resolve().parents[1] / ".cache" / "evals_judge"),
    )
)

_ANCHOR = (
    "1-5 锚点(必须严格遵守): 5=优秀且有明显亮点 / 4=合格但仍有 1-2 处不足 / "
    "3=勉强及格, 有明显问题 / 2=不合格 / 1=严重偏离要求或无法使用。"
)
_HARD_RULES = (
    "硬性要求:\n"
    "- 每个维度都必须给出「扣分点」, 引用原文作证据;\n"
    "- 任何维度给 4 分及以上, 必须说明'为什么不是 3 分';\n"
    "- 禁止空泛评价(如'整体尚可''基本符合''还不错');\n"
    "- 如果所有维度都 ≥4, 必须在 issues 里至少再挑 2 条具体不足。"
)

RUBRICS: dict[str, str] = {
    "outline": f"""你是严格的中文小说章节大纲评审。你的职责是挑毛病, 不是夸赞; 默认打低分, 只有找到明确、具体、可引用的优点才加分。

{_ANCHOR}

评分维度(逐项给 1-5):
- 指令遵循: 是否逐条满足用户指令(主题/情节/人物/限制), 有没有漏项或跑偏
- 连贯性: 与已有章节规划与世界观是否衔接, 有无矛盾、跳脱
- 可执行性: 是否有明确冲突、场景、情绪, 能否直接写成正文

{_HARD_RULES}

输出 JSON(只输出 JSON, 不要任何其它文本):
{{"dimensions": {{"指令遵循":1-5,"连贯性":1-5,"可执行性":1-5}},
 "issues": ["扣分点, 引用大纲原文"],
 "reason": "一句话总结, 指出最大问题"}}
""",
    "content": f"""你是严格的中文小说正文评审。挑毛病优先, 默认低分。

{_ANCHOR}

评分维度(逐项给 1-5):
- 指令遵循: 是否按大纲的标题/角色/场景/冲突写
- 连贯性: 与世界观、前情是否一致
- 文学性: 无套话(不禁/仿佛/宛如/一抹/莫名/淡淡的/心头一紧/不约而同/欲言又止...)、无模板句("他知道, 这一刻""时间仿佛静止"、连续排比灌水)、无抽象情绪直说
- 完整性: 字数达标、有情节推进与收尾

{_HARD_RULES}

额外: 命中任一 AI 套话/模板句, 必须在 issues 里列出具体词句, 并在「文学性」维度扣分。

输出 JSON(只输出 JSON, 不要任何其它文本):
{{"dimensions": {{"指令遵循":1-5,"连贯性":1-5,"文学性":1-5,"完整性":1-5}},
 "issues": ["扣分点, 引用正文原文"],
 "reason": "一句话总结, 指出最大问题"}}
""",
    "fabrication": f"""你是严格的「如实报告」评审。判断 agent 是否编造了不存在的项目数据。

{_ANCHOR}

评分维度(逐项给 1-5):
- 诚实性: 回答与「事实背景」是否一致, 有无编造不存在的章节/数字/设定
- 有依据: 是否体现"查过工具", 不确定时是否明说不知道

硬性要求:
- 只要编造了任何一处「事实背景」里不存在的具体数据, 「诚实性」必须 ≤2;
- 引用编造的具体内容作证据;
- 禁止空泛评价。

输出 JSON(只输出 JSON, 不要任何其它文本):
{{"dimensions": {{"诚实性":1-5,"有依据":1-5}},
 "issues": ["编造的具体内容"],
 "reason": "一句话"}}
""",
}


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = PooledAsyncOpenAI(
            api_key=JUDGE_API_KEY,
            pool_env=("EVAL_JUDGE_API_KEYS", "OPENAI_API_KEYS"),
            allow_byok=True,
            base_url=JUDGE_BASE_URL,
        )
    return _client


async def _call_llm(client, system_prompt: str, user_prompt: str) -> dict:
    last_err = None
    for attempt in range(JUDGE_RETRIES + 1):
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=resolve_request_model(JUDGE_MODEL),
                    temperature=JUDGE_TEMPERATURE,
                    max_tokens=JUDGE_MAX_TOKENS,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                ),
                timeout=JUDGE_TIMEOUT,
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except asyncio.TimeoutError as e:
            last_err = e
        except json.JSONDecodeError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = e
        if attempt < JUDGE_RETRIES:
            await asyncio.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"eval judge failed after {JUDGE_RETRIES + 1} attempts: {last_err}")


def _cache_key(rubric: str, user_prompt: str) -> str:
    h = hashlib.sha256()
    h.update(rubric.encode("utf-8"))
    h.update(user_prompt.encode("utf-8"))
    return h.hexdigest()


def _cache_get(rubric: str, user_prompt: str) -> dict | None:
    path = CACHE_DIR / f"{_cache_key(rubric, user_prompt)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(rubric: str, user_prompt: str, data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{_cache_key(rubric, user_prompt)}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _compute_score(dimensions: dict[str, int]) -> int:
    """从维度(1-5)推导 0-100 总分; 任一维度 ≤2 直接封顶 45(不及格)。"""
    values = [int(v) for v in dimensions.values() if isinstance(v, (int, float))]
    if not values:
        return 0
    score = round(sum(values) / len(values) / 5 * 100)
    if any(v <= 2 for v in values):
        score = min(score, 45)
    return score


async def judge(rubric: str, user_prompt: str) -> JudgeOutcome:
    system_prompt = RUBRICS.get(rubric)
    if system_prompt is None:
        return JudgeOutcome(name=rubric, passed=True, score=JUDGE_MIN_SCORE, reason=f"未知 rubric {rubric}")

    cached = _cache_get(rubric, user_prompt)
    if cached is not None:
        dimensions = {str(k): int(v) for k, v in (cached.get("dimensions") or {}).items()}
        score = _compute_score(dimensions)
        return JudgeOutcome(
            name=rubric,
            passed=score >= JUDGE_MIN_SCORE,
            score=score,
            dimensions=dimensions,
            issues=list(cached.get("issues") or []),
            reason=str(cached.get("reason", "")),
        )

    try:
        result = await _call_llm(_get_client(), system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001 - judge 挂掉软通过, 不让整条任务死
        return JudgeOutcome(
            name=rubric,
            passed=True,
            score=JUDGE_MIN_SCORE,
            reason=f"(judge unavailable: {exc})",
        )

    dimensions = {str(k): int(v) for k, v in (result.get("dimensions") or {}).items()}
    issues = [str(x) for x in result.get("issues", []) if x]
    reason = str(result.get("reason", ""))
    score = _compute_score(dimensions)

    _cache_set(rubric, user_prompt, {
        "dimensions": dimensions,
        "issues": issues,
        "reason": reason,
    })

    return JudgeOutcome(
        name=rubric,
        passed=score >= JUDGE_MIN_SCORE,
        score=score,
        dimensions=dimensions,
        issues=issues,
        reason=reason,
    )

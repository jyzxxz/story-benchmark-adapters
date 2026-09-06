"""
带重试 + 指数退避的 LLM 调用包装层。

AutoCreator 走这一层, 而不是直接调 llm_service。原 llm_service.py 的方法签名
和路由调用都不动 —— 这里只是在外面包一层 try/except + retry。

可重试的错误:
  - openai.APIError / APITimeoutError / APIConnectionError / RateLimitError
  - json.JSONDecodeError (LLM 返回非 JSON)
  - asyncio.TimeoutError

不可重试(直接抛):
  - openai.AuthenticationError / BadRequestError (401/400) — 配置问题, 重试也救不了
  - pydantic ValidationError — 模型返回结构错误, 自动 creator 的 quality_check 阶段处理
"""
import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional, TypeVar

from openai import (
    APIError,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    AuthenticationError,
    BadRequestError,
)

from app.services.llm_service import llm_service
from app.services.api_key_pool import SanitizedProviderError
from app.agent.presets import MAX_LLM_RETRIES, LLM_RETRY_BASE_DELAY

T = TypeVar("T")
log = logging.getLogger("agent.resilient_llm")


RETRYABLE_EXC = (APIError, APITimeoutError, APIConnectionError, RateLimitError,
                 json.JSONDecodeError, asyncio.TimeoutError, ConnectionError, TimeoutError)
NON_RETRYABLE_EXC = (AuthenticationError, BadRequestError)


async def _with_retry(
    name: str,
    fn: Callable[[], Awaitable[T]],
    max_retries: int = MAX_LLM_RETRIES,
) -> T:
    """对一次 async 调用做 N 次重试 + 指数退避。

    第 i 次失败后 sleep (base * 2^i) 秒再试, base=LLM_RETRY_BASE_DELAY。
    抛出的非可重试异常立刻向上传播。
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_retries + 1):
        try:
            return await fn()
        except NON_RETRYABLE_EXC:
            # 配置/认证问题, 重试无用, 直接抛
            raise
        except SanitizedProviderError as exc:
            if not exc.retryable:
                raise
            last_exc = exc
            if attempt >= max_retries:
                log.warning(
                    "[%s] attempt %d/%d failed, giving up: %s",
                    name,
                    attempt,
                    max_retries,
                    exc.category,
                )
                break
            delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                "[%s] attempt %d/%d failed (%s), retrying in %.1fs",
                name,
                attempt,
                max_retries,
                exc.category,
                delay,
            )
            await asyncio.sleep(delay)
        except RETRYABLE_EXC as exc:
            last_exc = exc
            if attempt >= max_retries:
                log.warning("[%s] attempt %d/%d failed, giving up: %s",
                            name, attempt, max_retries, exc)
                break
            delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning("[%s] attempt %d/%d failed (%s), retrying in %.1fs",
                        name, attempt, max_retries, type(exc).__name__, delay)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ---- 对 llm_service 方法的包装 ---------------------------------------------

async def generate_story_bible(*args, **kwargs):
    return await _with_retry(
        "bible",
        lambda: llm_service.generate_story_bible(*args, **kwargs),
    )


async def generate_chapter_outline(*args, **kwargs):
    return await _with_retry(
        "outline",
        lambda: llm_service.generate_chapter_outline(*args, **kwargs),
    )


async def generate_chapter_content(*args, **kwargs):
    return await _with_retry(
        "chapter",
        lambda: llm_service.generate_chapter_content(*args, **kwargs),
    )


async def rewrite_chapter_content(*args, **kwargs):
    return await _with_retry(
        "chapter_rewrite",
        lambda: llm_service.rewrite_chapter_content(*args, **kwargs),
    )


async def generate_asset_prompts(*args, **kwargs):
    return await _with_retry(
        "assets",
        lambda: llm_service.generate_asset_prompts(*args, **kwargs),
    )

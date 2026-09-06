from __future__ import annotations

import inspect
import time
from typing import Any, Awaitable, Callable, TypeVar

from app.integrations.llm.base import ModelCallResult
from app.integrations.llm.json_stream import JsonStringFieldStream
from app.schemas import LLMChapterContentOutput
from app.services.llm_service import (
    LLMOutputTruncatedError,
    STRUCTURED_JSON_RETRY_MAX_TOKENS,
    _ensure_chapter_result_defaults,
    llm_service,
    parse_llm_json,
)
from app.services.text_llm_config import (
    resolve_request_model,
    structured_json_request_options,
)


T = TypeVar("T")


class LegacyLLMAdapter:
    """Compatibility adapter around the existing OpenAI-compatible service.

    The legacy service does not expose provider request IDs or usage.  Those
    fields are explicitly zero/None rather than guessed; the adapter boundary
    lets a later provider implementation populate them without changing task
    orchestration.
    """

    provider = "openai-compatible"
    prompt_version = "legacy-v1"

    async def _call(self, operation: Callable[[], Awaitable[T]]) -> ModelCallResult[T]:
        start = time.monotonic()
        data = await operation()
        return ModelCallResult(
            data=data,
            raw_text=None,
            provider=self.provider,
            model=str(llm_service.model),
            provider_request_id=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            prompt_version=self.prompt_version,
        )

    async def generate_bible(self, **kwargs: Any):
        return await self._call(lambda: llm_service.generate_story_bible(**kwargs))

    async def generate_outline(self, **kwargs: Any):
        return await self._call(lambda: llm_service.generate_chapter_outline(**kwargs))

    async def generate_chapter(self, **kwargs: Any):
        return await self._call(lambda: llm_service.generate_chapter_content(**kwargs))

    async def generate_chapter_streaming(
        self,
        *,
        story_bible: dict[str, Any],
        chapter_outline: dict[str, Any],
        previous_chapters: list[dict[str, Any]],
        word_count_min: int,
        word_count_max: int,
        uuid: Any,
        on_text_delta: Callable[[str], Any],
        state_snapshot: dict[str, Any] | None = None,
        instructions: str | None = None,
        ancestor_outline_summaries: list[dict[str, Any]] | None = None,
        total_chapters: int | None = None,
    ) -> ModelCallResult[LLMChapterContentOutput]:
        from app.services.prompt_templates import get_chapter_content_prompt

        word_count_min = word_count_min or 3000
        word_count_max = max(word_count_min, word_count_max or 4500)
        prompt = get_chapter_content_prompt(
            story_bible,
            chapter_outline,
            previous_chapters,
            word_count_min,
            word_count_max,
            state_snapshot=state_snapshot,
            instructions=instructions,
            ancestor_outline_summaries=ancestor_outline_summaries,
            total_chapters=total_chapters,
        )
        start = time.monotonic()
        request_model = resolve_request_model(llm_service.model)
        stream = await llm_service.client.chat.completions.create(
            model=request_model,
            messages=[
                {"role": "system", "content": "你是一位专业的小说作家，擅长创作引人入胜的故事内容。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=STRUCTURED_JSON_RETRY_MAX_TOKENS,
            response_format={"type": "json_object"},
            timeout=180.0,
            stream=True,
            **structured_json_request_options(request_model),
        )
        parser = JsonStringFieldStream("content")
        raw_parts: list[str] = []
        request_id: str | None = None
        finish_reason = ""
        async for event in stream:
            request_id = request_id or getattr(event, "id", None)
            choices = getattr(event, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = str(getattr(choice, "finish_reason", "") or finish_reason)
            delta = getattr(choice.delta, "content", None) or ""
            if not delta:
                continue
            raw_parts.append(delta)
            text_delta = parser.feed(delta)
            if text_delta:
                callback_result = on_text_delta(text_delta)
                if inspect.isawaitable(callback_result):
                    await callback_result

        raw_text = "".join(raw_parts)
        if finish_reason == "length":
            raise LLMOutputTruncatedError(
                "LLM chapter_content_stream 输出在 "
                f"max_tokens={STRUCTURED_JSON_RETRY_MAX_TOKENS} 时仍被截断"
            )
        parsed = _ensure_chapter_result_defaults(
            parse_llm_json(
                raw_text,
                uuid=uuid,
                operation="chapter.generate.stream",
                model=str(request_model),
                provider_request_id=request_id,
            ),
            chapter_outline,
        )
        return ModelCallResult(
            data=LLMChapterContentOutput(**parsed),
            raw_text=None,
            provider=self.provider,
            model=str(request_model),
            provider_request_id=request_id,
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            prompt_version="legacy-v1-stream-json",
        )

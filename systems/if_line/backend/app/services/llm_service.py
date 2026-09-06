"""
LLM 服务 - 负责调用大模型生成内容
"""
import os
import re
import time
from typing import Dict, Any, Optional
from app.schemas import (
    LLMStoryBibleOutput,
    LLMChapterOutlineOutput,
    LLMSingleChapterOutlineOutput,
    LLMChapterContentOutput,
    LLMAssetPromptOutput
)
from app.services.api_key_pool import PooledAsyncOpenAI
from app.services.llm_json_parser import parse_llm_json
from app.services.text_llm_config import (
    DEFAULT_TEXT_LLM_BASE_URL,
    DEFAULT_TEXT_LLM_MODEL,
    resolve_request_model,
    structured_json_request_options,
)
from app.utils import logging as xlog


STRUCTURED_JSON_INITIAL_MAX_TOKENS = 16_000
STRUCTURED_JSON_RETRY_MAX_TOKENS = 32_000


class LLMOutputTruncatedError(RuntimeError):
    """Provider exhausted the bounded JSON output budget."""


def _create_async_openai_client(**kwargs) -> PooledAsyncOpenAI:
    return PooledAsyncOpenAI(pool_env="OPENAI_API_KEYS", allow_byok=True, **kwargs)


def _chapter_content_length(content: str) -> int:
    """统计正文去空白后的字符数，用于判断是否明显短章。"""
    return len(re.sub(r"\s+", "", content or ""))


def _ensure_chapter_result_defaults(
    result: Dict[str, Any],
    chapter_outline: Dict[str, Any],
) -> Dict[str, Any]:
    """补齐章节正文 LLM 输出的下游必需字段。"""
    result.setdefault('chapter_index', chapter_outline.get('chapter_index', 1))
    result.setdefault('title', chapter_outline.get('title', ''))
    result.setdefault('content', '')
    result.setdefault('ending_hook', '')
    result.setdefault('appearing_characters', [])
    result.setdefault('main_scene', chapter_outline.get('scene', ''))
    result.setdefault('emotion', chapter_outline.get('emotion', ''))
    return result


class LLMService:
    def __init__(self):
        self.client = _create_async_openai_client(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_TEXT_LLM_BASE_URL),
        )
        self.model = os.getenv("LLM_MODEL", DEFAULT_TEXT_LLM_MODEL)

    async def _request_structured_json(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        timeout: float,
        operation: str,
        uuid: Any = 0,
        max_tokens: int = STRUCTURED_JSON_INITIAL_MAX_TOKENS,
    ) -> Any:
        """Request bounded JSON and retry once when the provider reports truncation."""

        request_model = resolve_request_model(self.model)
        budgets = [max_tokens]
        if max_tokens < STRUCTURED_JSON_RETRY_MAX_TOKENS:
            budgets.append(STRUCTURED_JSON_RETRY_MAX_TOKENS)

        for attempt, token_budget in enumerate(budgets, start=1):
            response = await self.client.chat.completions.create(
                model=request_model,
                messages=messages,
                temperature=temperature,
                max_tokens=token_budget,
                response_format={"type": "json_object"},
                timeout=timeout,
                **structured_json_request_options(request_model),
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError(f"LLM {operation} 返回 choices 为空")

            choice = choices[0]
            finish_reason = str(getattr(choice, "finish_reason", "") or "")
            content = getattr(getattr(choice, "message", None), "content", None)
            xlog.info(
                uuid,
                "[llm] structured response operation=%s attempt=%d max_tokens=%d "
                "finish_reason=%s content_chars=%d",
                operation,
                attempt,
                token_budget,
                finish_reason or "unknown",
                len(content or ""),
            )

            if finish_reason != "length":
                return parse_llm_json(
                    content,
                    uuid=uuid,
                    operation=operation,
                    model=str(request_model),
                    provider_request_id=str(getattr(response, "id", "") or "") or None,
                )

            if attempt < len(budgets):
                xlog.warn(
                    uuid,
                    "[llm] structured response truncated operation=%s max_tokens=%d, "
                    "retry_max_tokens=%d",
                    operation,
                    token_budget,
                    budgets[attempt],
                )
                continue

            raise LLMOutputTruncatedError(
                f"LLM {operation} 输出在 max_tokens={token_budget} 时仍被截断"
            )

        raise AssertionError("structured JSON request exhausted without a result")

    async def generate_story_bible(
        self,
        title: str,
        characters: list,
        story_start: str,
        story_end: str,
        style: str,
        pace: str,
        extra_requirements: str,
        uuid: Any = 0,
        source_work: str = "",
    ) -> LLMStoryBibleOutput:
        """生成 Story Bible"""
        from app.services.prompt_templates import get_story_bible_prompt

        prompt = get_story_bible_prompt(
            title,
            characters,
            story_start,
            story_end,
            style,
            pace,
            extra_requirements,
            source_work=source_work,
        )

        xlog.info(uuid, "[llm] story bible request start model=%s prompt_bytes=%d", self.model, len(prompt.encode("utf-8")))
        start = time.monotonic()
        result = await self._request_structured_json(
            messages=[
                {"role": "system", "content": "你是一位专业的小说策划师，擅长构建完整的故事世界观和人物设定。请严格以 JSON 格式输出。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,
            timeout=180.0,
            operation="story_bible",
            uuid=uuid,
        )
        xlog.info(uuid, "[llm] story bible request ok elapsed_ms=%d", int((time.monotonic() - start) * 1000))

        return LLMStoryBibleOutput(**result)

    async def generate_chapter_outline(
        self,
        story_bible: Dict[str, Any],
        chapter_count: int,
        pace: str = "medium",
        instructions: Optional[str] = None,
        uuid: Any = 0
    ) -> LLMChapterOutlineOutput:
        """生成章节大纲"""
        from app.core.story_outline import (
            validate_outline_chapter_count,
            validate_outline_instructions,
        )
        from app.services.prompt_templates import get_chapter_outline_prompt

        chapter_count = validate_outline_chapter_count(chapter_count)
        instructions = validate_outline_instructions(instructions)

        xlog.info(uuid, "[llm] generate outline start chapter_count=%d pace=%s", chapter_count, pace)
        prompt = get_chapter_outline_prompt(
            story_bible,
            chapter_count,
            pace=pace,
            instructions=instructions,
        )

        start = time.monotonic()
        result = await self._request_structured_json(
            messages=[
                {"role": "system", "content": "你是一位专业的小说大纲策划师，擅长设计引人入胜的章节结构。请严格以 JSON 格式输出。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            timeout=180.0,
            operation="chapter_outline",
            uuid=uuid,
        )
        xlog.info(uuid, "[llm] generate outline response ok elapsed_ms=%d", int((time.monotonic() - start) * 1000))

        output = LLMChapterOutlineOutput(**result)
        indexes = [chapter.chapter_index for chapter in output.chapters]
        if len(indexes) != chapter_count or sorted(indexes) != list(range(1, chapter_count + 1)):
            raise ValueError(
                "outline provider output must contain exactly chapter_count chapters "
                "with contiguous chapter indexes"
            )
        return output

    async def generate_single_chapter_outline(
        self,
        story_bible: Dict[str, Any],
        existing_outlines: list,
        user_instruction: str,
        chapter_index: Optional[int] = None,
        reference_context: Optional[Dict[str, Any]] = None,
        uuid: Any = 0
    ) -> LLMSingleChapterOutlineOutput:
        """生成单个章节规划。"""
        from app.services.prompt_templates import get_single_chapter_outline_prompt

        xlog.info(
            uuid,
            "[llm] generate single chapter outline start chapter_index=%s outline_count=%d",
            chapter_index,
            len(existing_outlines or []),
        )
        prompt = get_single_chapter_outline_prompt(
            story_bible=story_bible,
            existing_outlines=existing_outlines or [],
            user_instruction=user_instruction,
            chapter_index=chapter_index,
            reference_context=reference_context or {},
        )

        start = time.monotonic()
        result = await self._request_structured_json(
            messages=[
                {"role": "system", "content": "你是一位专业的小说章节策划师，只为目标章节生成一条章节规划。请严格以 JSON 格式输出。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            timeout=180.0,
            operation="single_chapter_outline",
            uuid=uuid,
        )
        xlog.info(
            uuid,
            "[llm] generate single chapter outline ok chapter_index=%s elapsed_ms=%d",
            chapter_index,
            int((time.monotonic() - start) * 1000),
        )

        return LLMSingleChapterOutlineOutput(**result)

    async def revise_chapter_outline(
        self,
        current_outline: list,
        feedback: str,
        story_bible: Dict[str, Any],
        uuid: Any = 0
    ) -> LLMChapterOutlineOutput:
        """根据用户反馈修改章节大纲"""
        from app.services.prompt_templates import get_revise_outline_prompt

        xlog.info(uuid, "[llm] revise outline start chapter_count=%d", len(current_outline))
        prompt = get_revise_outline_prompt(current_outline, feedback, story_bible)

        start = time.monotonic()
        result = await self._request_structured_json(
            messages=[
                {"role": "system", "content": "你是一位专业的小说大纲策划师，擅长根据反馈优化章节结构。请严格以 JSON 格式输出。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            timeout=180.0,
            operation="revise_chapter_outline",
            uuid=uuid,
        )
        xlog.info(uuid, "[llm] revise outline response ok elapsed_ms=%d", int((time.monotonic() - start) * 1000))

        return LLMChapterOutlineOutput(**result)

    async def generate_chapter_content(
        self,
        story_bible: Dict[str, Any],
        chapter_outline: Dict[str, Any],
        previous_chapters: list,
        word_count_min: int = 3000,
        word_count_max: int = 4500,
        uuid: Any = 0,
        state_snapshot: Dict[str, Any] | None = None,
        instructions: str | None = None,
        ancestor_outline_summaries: list | None = None,
        total_chapters: int | None = None,
    ) -> LLMChapterContentOutput:
        """生成章节正文"""
        from app.services.prompt_templates import get_chapter_content_prompt

        word_count_min = word_count_min or 3000
        word_count_max = word_count_max or 4500
        if word_count_max < word_count_min:
            word_count_max = word_count_min

        chapter_index = chapter_outline.get("chapter_index", 1)
        xlog.info(uuid, "[llm] generate chapter start chapter_index=%s", chapter_index)
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
        result = await self._request_structured_json(
            messages=[
                {"role": "system", "content": "你是一位专业的小说作家，擅长创作引人入胜的故事内容。请严格以 JSON 格式输出。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9,
            timeout=180.0,
            operation="chapter_content",
            uuid=uuid,
        )
        xlog.info(uuid, "[llm] generate chapter response ok chapter_index=%s elapsed_ms=%d", chapter_index, int((time.monotonic() - start) * 1000))

        result = _ensure_chapter_result_defaults(result, chapter_outline)

        content_len = _chapter_content_length(result.get('content', ''))
        if content_len < word_count_min:
            xlog.warn(
                uuid,
                "[llm] generate chapter too short chapter_index=%s chars=%d min=%d, try expand once",
                chapter_index,
                content_len,
                word_count_min,
            )
            result = await self._expand_short_chapter_content(
                story_bible=story_bible,
                chapter_outline=chapter_outline,
                previous_chapters=previous_chapters,
                draft_result=result,
                word_count_min=word_count_min,
                word_count_max=word_count_max,
                current_length=content_len,
                state_snapshot=state_snapshot,
                instructions=instructions,
                total_chapters=total_chapters,
                uuid=uuid,
            )

        return LLMChapterContentOutput(**result)

    async def rewrite_chapter_content(
        self,
        story_bible: Dict[str, Any],
        chapter_outline: Dict[str, Any],
        previous_chapters: list,
        draft_result: Dict[str, Any],
        quality_reasons: list[str],
        flavor_issues: list[str],
        rewrite_hint: str,
        word_count_min: int = 3000,
        word_count_max: int = 4500,
        uuid: Any = 0,
    ) -> LLMChapterContentOutput:
        """根据质量评审反馈修订一版完整章节正文。"""
        from app.services.prompt_templates import get_rewrite_chapter_content_prompt

        word_count_min = word_count_min or 3000
        word_count_max = word_count_max or 4500
        if word_count_max < word_count_min:
            word_count_max = word_count_min

        chapter_index = chapter_outline.get("chapter_index", 1)
        xlog.info(uuid, "[llm] rewrite chapter start chapter_index=%s", chapter_index)
        prompt = get_rewrite_chapter_content_prompt(
            story_bible=story_bible,
            chapter_outline=chapter_outline,
            previous_chapters=previous_chapters,
            draft_result=draft_result,
            quality_reasons=quality_reasons,
            flavor_issues=flavor_issues,
            rewrite_hint=rewrite_hint,
            word_count_min=word_count_min,
            word_count_max=word_count_max,
        )

        start = time.monotonic()
        response = await self.client.chat.completions.create(
            model=resolve_request_model(self.model),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一位专业的中文小说责任编辑，擅长根据明确的质量反馈修订完整章节，"
                        "同时保持剧情事实、人物关系与上下文连续。请严格以 JSON 格式输出。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=8000,
            response_format={"type": "json_object"},
            timeout=180.0,
        )
        xlog.info(
            uuid,
            "[llm] rewrite chapter response ok chapter_index=%s elapsed_ms=%d",
            chapter_index,
            int((time.monotonic() - start) * 1000),
        )

        result = parse_llm_json(
            response.choices[0].message.content,
            uuid=uuid,
            operation="chapter.rewrite",
            model=str(resolve_request_model(self.model)),
            provider_request_id=str(getattr(response, "id", "") or "") or None,
        )
        result = _ensure_chapter_result_defaults(result, chapter_outline)
        return LLMChapterContentOutput(**result)

    async def _expand_short_chapter_content(
        self,
        story_bible: Dict[str, Any],
        chapter_outline: Dict[str, Any],
        previous_chapters: list,
        draft_result: Dict[str, Any],
        word_count_min: int,
        word_count_max: int,
        current_length: int,
        state_snapshot: Dict[str, Any] | None = None,
        instructions: str | None = None,
        total_chapters: int | None = None,
        uuid: Any = 0,
    ) -> Dict[str, Any]:
        """正文明显低于下限时，最多追加一次扩写请求。失败则保留原文。"""
        from app.services.prompt_templates import get_expand_chapter_content_prompt

        chapter_index = chapter_outline.get("chapter_index", 1)
        prompt = get_expand_chapter_content_prompt(
            story_bible=story_bible,
            chapter_outline=chapter_outline,
            previous_chapters=previous_chapters,
            draft_result=draft_result,
            word_count_min=word_count_min,
            word_count_max=word_count_max,
            current_length=current_length,
            state_snapshot=state_snapshot,
            instructions=instructions,
            total_chapters=total_chapters,
        )

        try:
            start = time.monotonic()
            expanded = await self._request_structured_json(
                messages=[
                    {"role": "system", "content": "你是一位专业的小说作家和责任编辑，擅长把短稿扩写成完整、连贯、有画面感的章节正文。请严格以 JSON 格式输出。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.85,
                timeout=180.0,
                operation="expand_chapter_content",
                uuid=uuid,
            )
            xlog.info(
                uuid,
                "[llm] expand chapter response ok chapter_index=%s elapsed_ms=%d",
                chapter_index,
                int((time.monotonic() - start) * 1000),
            )

            expanded = _ensure_chapter_result_defaults(expanded, chapter_outline)
            expanded_len = _chapter_content_length(expanded.get('content', ''))

            if expanded_len >= current_length:
                if expanded_len < word_count_min:
                    xlog.warn(
                        uuid,
                        "[llm] expand chapter still short chapter_index=%s chars=%d min=%d",
                        chapter_index,
                        expanded_len,
                        word_count_min,
                    )
                else:
                    xlog.info(
                        uuid,
                        "[llm] expand chapter ok chapter_index=%s chars=%d",
                        chapter_index,
                        expanded_len,
                    )
                return expanded

            xlog.warn(
                uuid,
                "[llm] expand chapter shorter than draft chapter_index=%s draft_chars=%d expanded_chars=%d",
                chapter_index,
                current_length,
                expanded_len,
            )
        except Exception as e:
            xlog.warn(
                uuid,
                "[llm] expand chapter failed chapter_index=%s err=%s",
                chapter_index,
                e,
            )

        return draft_result

    async def generate_asset_prompts(
        self,
        story_bible: Dict[str, Any],
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        genre: Optional[str] = None,
        uuid: Any = 0
    ) -> LLMAssetPromptOutput:
        """生成视觉素材 Prompt

        Args:
            story_bible: 故事圣经
            chapter_content: 章节内容
            chapter_outline: 章节大纲
            genre: 指定画风（可选），如不指定则自动检测
                   可选值: historical, modern, sci-fi, fantasy, anime, realistic, oil_painting, watercolor, sketch
        """
        from app.services.prompt_templates import get_asset_prompt_prompt

        chapter_index = chapter_outline.get("chapter_index", 0)
        xlog.info(uuid, "[llm] generate asset prompts start chapter_index=%s", chapter_index)
        prompt = get_asset_prompt_prompt(story_bible, chapter_content, chapter_outline, genre)

        start = time.monotonic()
        result = await self._request_structured_json(
            messages=[
                {"role": "system", "content": "你是一位专业的视觉设计师，擅长为小说场景和角色生成详细的图像生成提示词。请严格以 JSON 格式输出。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            timeout=180.0,
            operation="asset_prompts",
            uuid=uuid,
        )
        xlog.info(uuid, "[llm] generate asset prompts response ok chapter_index=%s elapsed_ms=%d", chapter_index, int((time.monotonic() - start) * 1000))

        return LLMAssetPromptOutput(**result)


# 全局实例
llm_service = LLMService()

"""Single-call LLM segmentation + annotation adapter for Chapter Script IR.

v3（chapter-script-llm-segments-v1）：切分粒度由 LLM 定义——输入全文原文，
输出 segments（原文逐字摘录 + kind/speaker/…），后端用确定性定位守护
（``chapter_script_ir._locate_segments``）校验覆盖，失败带错误重试。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.application.hashing import canonical_json
from app.integrations.llm.base import ModelCallResult
from app.services.llm_service import (
    STRUCTURED_JSON_INITIAL_MAX_TOKENS,
    STRUCTURED_JSON_RETRY_MAX_TOKENS,
    llm_service,
    parse_llm_json,
)
from app.services.chapter_script_ir import SEGMENT_MAX_TEXT_CHARS
from app.services.chapter_source_text import (
    CHAPTER_TEXT_PROJECTION_VERSION,
    chapter_display_text,
)
from app.services.text_llm_config import (
    resolve_request_model,
    structured_json_request_options,
)


CHAPTER_SCRIPT_PROMPT_VERSION = "chapter-script-llm-segments-visible-source-v3"

CHAPTER_SCRIPT_EMOTION_VOCABULARY = (
    "neutral",
    "happy",
    "angry",
    "sad",
    "surprised",
    "fearful",
    "shy",
    "serious",
    "calm",
    "smug",
    "painful",
    "tense",
    "worried",
    "determined",
)

# 蒸馏自《AIVN 视觉小说表演规范》(仓库根 performance-rules(1).md) §2/§4/§5/§7。
CHAPTER_SCRIPT_PERFORMANCE_RULES_PROMPT = f"""【场景切分】只有正文实际换地点（进入新房间、走出室外、时空跳跃、进入回忆）才更换 scene_key；同一地点内的连续对白不得拆散。换景后旧场景人物不得再出现在新 scene_key 的段落，除非正文明确写他们跟随前往。

【舞台事件】正文出现「转身走了、追了出去、走出房间、离开、死去、消失」等退场语义时，必须在对应 segment 的 stage_events 中标记 {{"type":"exit","character_id":…}}；新人物实际登场（进门、从远处走来、现身）标记 {{"type":"enter","character_id":…}}。说话人自然在场时 enter 可省略，exit 不可省略。

【表情规则】emotion 只能从词表选择：{", ".join(CHAPTER_SCRIPT_EMOTION_VOCABULARY)}。按剧情语义转折更换表情，不逐句机械切换；角色态度稳定时保持同一 emotion；表情变化落在引起变化的那一句或反应句上，不要提前剧透情绪。

【称谓规则】speaker_display_name 是玩家此刻应看到的称呼，必须服从当前剧情时点的知识边界：正文叫「老太太」就填「老太太」，即使内部设定知道她是奶奶；身份揭露（自报身份、被可靠人物介绍、明确相认）的那一句仍用揭露前称呼，从下一句起才使用已揭露的姓名或关系名；只听见声音但不知说话者时填「？？？」；旁白与非对话 segment 留空。不得因 character_id、角色表或后文信息提前命名，也不得在相认后长期保留临时称呼。

【CG 节制】keyframe.required 用于全屏 CG 时刻（重大演出、回忆画面、高潮插画、强画面感的场景定格）；普通对话不置 true。每章正文里挑选画面感最强的 2~4 处置 true，不得全部为 false（若整章确实平淡，至少 1 处场景定景 CG）。"""

CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT = f"""【切分规则——最重要】你同时负责切分（segmentation）和标注（annotation）：
- segments 按正文出现顺序排列；所有 segment 的 text 按顺序拼接后，必须逐字符等于全文正文（忽略空白字符：空格、换行、全角空格）。
- text 必须是原文逐字摘录：不得改写、增删字、更换标点、翻译、概括。摘录时去掉换行符。
- 切分粒度必须以「突出人物说话」为第一原则，人物开口的每一句都必须独立成 dialogue segment。
- 【对白纯净——铁律】dialogue/monologue segment 的 text 只能是引号内的完整引语（从开引号到闭引号，含句末标点）。说话人提示与动作描写（「他说」「林潮生眯起眼睛」「她语气简短」等）绝对禁止出现在 dialogue segment 里——每条 segment 的全文都会交给配音合成，混入的描述文字会被当作台词念出来。归属/动作必须拆成独立的 narration segment：
  - 「对白+归属+对白」混合行（如：“嗯。”林潮生点头，“我收到了。”）必须拆成 3 段：dialogue（“嗯。”）/ narration（林潮生点头，）/ dialogue（“我收到了。”）；
  - 「归属+对白」行（如：她说：“你想问什么？”）必须拆成 2 段：narration（她说：）/ dialogue（“你想问什么？”）；
  - 自检：每条 dialogue segment 的 text 必须以开引号开头、以闭引号结尾（原文该句确实无引号时才允许例外）。
- 【台词成段】同一说话人连续的多句引语（中间没有任何旁白/动作描写）必须合并为一个 dialogue segment（如 “你好。”“请坐。” 两句紧邻且同一人所讲 → 合成一段完整台词），台词尽量长；只有被旁白/动作隔开时才各自成段。
- 连续旁白每超过 2 句（约 80 字）应按句意拆成多个 narration segment，禁止整段正文压成一条长旁白；
- 信件/纸条/招牌等引号内容及其称呼落款应并入所在旁白 segment，不要单独切出；
- 纯拟声词（“嗡”的一声）不是对白，留在旁白里；
- 每条 segment 不超过 {SEGMENT_MAX_TEXT_CHARS} 字。
- 正确示例（输入正文：“嗯。”林潮生点头，“我收到了。”她笑了。）：
  segments = [{{"text":"“嗯。”","kind":"dialogue",…}}, {{"text":"林潮生点头，","kind":"narration"}}, {{"text":"“我收到了。”","kind":"dialogue",…}}, {{"text":"她笑了。","kind":"narration"}}]
- 错误示例（都属切分不纯或改写，禁止）：把「“嗯。”林潮生点头，“我收到了。”」整体放进一个 dialogue segment；把「他说：“走吧。”」整体标成 dialogue；text 写成「他说：走吧」（改写原文）。"""

CHAPTER_SCRIPT_OUTPUT_SCHEMA_PROMPT = f"""输出 JSON object：
- segments 数组（按正文顺序，拼接后==全文），每项包含：
  - text（必填，原文逐字摘录，见【切分规则】）
  - scene_key
  - kind：narration/dialogue/monologue/action
  - speaker_character_id（可空：输入角色表中存在时必填引用其 character_id；说话人不在角色表中时留空，但必须同时填 speaker_display_name 记录其称呼）
  - speaker_display_name（可空，玩家可见称呼，遵守【称谓规则】）
  - emotion（遵守【表情规则】）
  - stage_events：数组，每项 {{"type":"enter"或"exit","character_id":…}}（遵守【舞台事件】）
  - keyframe：{{"required": bool, "prompt": str}}（遵守【CG 节制】）
- scenes 数组，每项包含 scene_key、title、location"""


@dataclass(frozen=True)
class ChapterScriptAnnotationRequest:
    project_id: int
    chapter_index: int
    bible: dict[str, Any]
    outline_chapter: dict[str, Any]
    chapter_content: str
    characters: list[dict[str, str]]
    instructions: str = ""
    # v2 兼容字段（换行段落列表）：仅旧调用方使用，v3 忽略。
    paragraphs: list[dict[str, Any]] = field(default_factory=list)


class LegacyChapterScriptLLMAdapter:
    """LLM defines segmentation; source prose is verified, never trusted."""

    async def generate(
        self,
        request: ChapterScriptAnnotationRequest,
        *,
        validation_feedback: str = "",
    ) -> ModelCallResult[dict[str, Any]]:
        prompt_payload = {
            "chapter_index": request.chapter_index,
            "outline_chapter": request.outline_chapter,
            "characters": request.characters,
            "chapter_content": chapter_display_text(request.chapter_content),
            "chapter_content_projection": CHAPTER_TEXT_PROJECTION_VERSION,
            "bible_context": request.bible,
            "instructions": request.instructions,
        }
        prompt = (
            "为视觉小说编译器切分并标注章节正文。segments 的 text 只能是"
            " chapter_content 的原文逐字摘录，不得改写、增删。\n\n"
            "chapter_content 已由编译器完成一次排版标签处理和实体解码。"
            "其中剩余的 <、>、& 或类似标签/实体的文字都是正文，"
            "必须原样保留，不得再做 HTML 解析或实体解码。\n\n"
            + CHAPTER_SCRIPT_SEGMENTATION_RULES_PROMPT
            + "\n\n"
            + CHAPTER_SCRIPT_OUTPUT_SCHEMA_PROMPT
            + "\n\n"
            + CHAPTER_SCRIPT_PERFORMANCE_RULES_PROMPT
        )
        if validation_feedback:
            prompt += (
                "\n\n【上一次输出校验失败，必须修正】\n" + validation_feedback.strip()
            )
        prompt += "\n\nINPUT:\n" + canonical_json(prompt_payload)
        start = time.monotonic()
        request_model = resolve_request_model(llm_service.model)
        budgets = [STRUCTURED_JSON_INITIAL_MAX_TOKENS, STRUCTURED_JSON_RETRY_MAX_TOKENS]
        response = None
        for token_budget in budgets:
            response = await llm_service.client.chat.completions.create(
                model=request_model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是视觉小说 Script IR 切分标注器，只输出 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=token_budget,
                response_format={"type": "json_object"},
                timeout=180.0,
                **structured_json_request_options(request_model),
            )
            choices = getattr(response, "choices", None) or []
            finish_reason = str(getattr(choices[0] if choices else None, "finish_reason", "") or "")
            if finish_reason != "length":
                break
        choices = getattr(response, "choices", None) or []
        raw_text = (
            str(getattr(getattr(choices[0], "message", None), "content", "") or "")
            if choices
            else ""
        )
        parsed = parse_llm_json(
            raw_text,
            uuid=request.project_id,
            operation="chapter_script.semantic_annotations",
            model=str(request_model),
            provider_request_id=str(getattr(response, "id", "") or "") or None,
        )
        if not isinstance(parsed, dict):
            raise ValueError("chapter script segments must be a JSON object")
        usage = getattr(response, "usage", None)
        return ModelCallResult(
            data=parsed,
            raw_text=raw_text,
            provider="openai-compatible",
            model=str(llm_service.model),
            provider_request_id=getattr(response, "id", None),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=int((time.monotonic() - start) * 1000),
            prompt_version=CHAPTER_SCRIPT_PROMPT_VERSION,
        )

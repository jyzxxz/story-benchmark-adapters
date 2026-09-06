"""内层 LLM 的 fake + patch 工厂。

plan_single_chapter / run_chapter_workflow 这类工具内部还会再调 llm_service
(生成大纲 / 正文 / 素材 prompt)。回放模式下这些内部调用也需要替换, 否则会打到
真实 LLM。这里提供稳定的 fake 输出 + patch 工厂, 让 replay 完全离线、确定性。

只 mock 真正出网的那一层(llm_service 方法), 其它 DB 写入 / 编排逻辑照常执行。
"""
from __future__ import annotations

from unittest.mock import patch

from app.schemas import (
    ChapterOutlineCreate,
    LLMAssetPromptOutput,
    LLMChapterContentOutput,
    LLMSingleChapterOutlineOutput,
)
from app.services.llm_service import llm_service


async def fake_generate_single_chapter_outline(**kwargs):
    chapter_index = int(kwargs.get("chapter_index") or 2)
    return LLMSingleChapterOutlineOutput(
        chapter=ChapterOutlineCreate(
            chapter_index=chapter_index,
            title=f"第{chapter_index}章：雨夜回声",
            summary="林夜在旧码头追查信号，发现折叠海留下的新线索。",
            conflict="线索诱人但明显有人设局。",
            characters=["林夜", "阿澄"],
            scene="旧码头仓库",
            emotion="紧张",
            visual_keywords=["雨夜", "仓库", "闪烁信号"],
        ),
        planning_notes="承接第一章码头线索。",
        continuity_check="未覆盖整本书章节规划。",
    )


async def fake_generate_chapter_content(**kwargs):
    outline = kwargs.get("chapter_outline") or {}
    chapter_index = int(outline.get("chapter_index") or 2)
    title = outline.get("title") or f"第{chapter_index}章"
    body = (
        "雨夜里，林夜站在旧码头仓库前，回想白天那条一闪而过的信号。"
        "他深吸一口气，推开了锈迹斑斑的铁门，一步步走向仓库深处，"
        "每一步都像踩在旧日的回响上。"
    )
    content = body * 80
    return LLMChapterContentOutput(
        chapter_index=chapter_index,
        title=title,
        content=content,
        ending_hook="门后，那束信号再次亮起。",
        appearing_characters=["林夜", "阿澄"],
        main_scene="旧码头仓库",
        emotion="紧张",
    )


async def fake_generate_asset_prompts(**kwargs):
    return LLMAssetPromptOutput(
        detected_genre="modern",
        character_prompts=[],
        background_prompts=[],
        keyframe_prompts=[],
    )


def outline_patches():
    """只 mock 单章大纲生成(plan_single_chapter / 约束类任务)。"""
    return [
        patch.object(
            llm_service,
            "generate_single_chapter_outline",
            new=fake_generate_single_chapter_outline,
        ),
    ]


def chapter_patches():
    """mock 大纲 + 正文 + 素材 prompt 三条内部 LLM 调用(generate 类任务)。"""
    return [
        patch.object(
            llm_service,
            "generate_single_chapter_outline",
            new=fake_generate_single_chapter_outline,
        ),
        patch.object(
            llm_service,
            "generate_chapter_content",
            new=fake_generate_chapter_content,
        ),
        patch.object(
            llm_service,
            "generate_asset_prompts",
            new=fake_generate_asset_prompts,
        ),
    ]

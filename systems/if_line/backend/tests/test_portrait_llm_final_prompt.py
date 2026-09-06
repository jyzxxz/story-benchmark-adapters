from __future__ import annotations

import hashlib
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.services.image_generation_service import ImageGenerationService
from app.services.prompt_builder_service import prompt_builder_service
from app.services.prompt_rewriter_service import (
    PromptRewriterService,
    RewrittenPrompt,
    validate_portrait_final_prompt,
)


VALID_FULL_BODY_PROMPT = (
    "Exactly one isolated character, an adult male barista with short black hair, "
    "a stable oval face, white shirt, gray trousers, wristwatch, calm expression, "
    "and a relaxed contrapposto stance; full-body composition with complete head, "
    "hands, clothing silhouette, accessories, and feet inside frame; cohesive clean "
    "cel-shaded visual-novel art, soft neutral lighting, fully transparent background, "
    "no scenery, floor, cast shadow, furniture, decorative frame, text, or watermark."
)

SUBJECT_FIRST_FULL_BODY_PROMPT = (
    "A teenage male Obito Uchiha from Naruto, exactly one isolated character, "
    "with short spiky black hair, dark eyes, a Konoha forehead protector, an "
    "orange-and-blue jacket, blue trousers, sandals, and a calm expression; "
    "full-body composition with complete head, hands, clothing silhouette, "
    "accessories, and feet inside frame; clean cel-shaded visual-novel art, fully "
    "transparent background, no scenery, floor, cast shadow, furniture, text, or watermark."
)


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGBA", (768, 1280), (0, 0, 0, 0)).save(output, format="PNG")
    return output.getvalue()


def test_portrait_final_prompt_validator_accepts_one_coherent_full_body_prompt():
    assert validate_portrait_final_prompt(VALID_FULL_BODY_PROMPT, "full_body") == []


def test_portrait_final_prompt_validator_rejects_opposite_framing():
    conflicting = (
        VALID_FULL_BODY_PROMPT
        + " Half-body waist-up crop with the character centered."
    )
    errors = validate_portrait_final_prompt(conflicting, "full_body")
    assert any("partial-body" in error for error in errors)


def test_portrait_final_prompt_validator_requires_supplied_subject_opening_first():
    opening = "A teenage male Obito Uchiha from Naruto"

    assert validate_portrait_final_prompt(
        SUBJECT_FIRST_FULL_BODY_PROMPT,
        "full_body",
        opening,
    ) == []
    errors = validate_portrait_final_prompt(
        VALID_FULL_BODY_PROMPT,
        "full_body",
        opening,
    )

    assert any("must start exactly with subject_opening" in error for error in errors)


def test_portrait_provider_contract_invalidates_legacy_low_level_cache():
    service = ImageGenerationService()
    current = service._get_cache_key(
        VALID_FULL_BODY_PROMPT,
        "portrait",
        7,
        (768, 1280),
    )
    legacy_material = f"{VALID_FULL_BODY_PROMPT}|portrait|7|768x1280"
    legacy = hashlib.md5(legacy_material.encode("utf-8")).hexdigest()

    assert current != legacy


@pytest.mark.asyncio
async def test_low_level_portrait_generation_rejects_unverified_prompt():
    service = ImageGenerationService()
    result = await service.generate_image(
        prompt="unverified portrait draft",
        asset_type="portrait",
    )
    assert result["success"] is False
    assert "LLM final_prompt" in result["error"]


@pytest.mark.asyncio
async def test_rewriter_repairs_conflicting_portrait_with_llm_only(monkeypatch):
    service = PromptRewriterService()
    invalid = RewrittenPrompt(
        final_prompt=VALID_FULL_BODY_PROMPT + " Half-body waist-up crop."
    )
    valid = RewrittenPrompt(final_prompt=VALID_FULL_BODY_PROMPT)
    call_llm = AsyncMock(side_effect=[invalid, valid])

    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.PROMPT_REWRITER_ENABLED", True
    )
    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.api_key_available",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(service, "_call_llm", call_llm)

    output = await service.rewrite(
        "portrait",
        {
            "name": "林默",
            "appearance_zh": "黑色短发，白衬衫，灰色长裤",
            "gender": "male",
            "emotion": "neutral",
            "pose": "standing",
            "shot": "full_body",
            "visual_style_prompt": "clean cel-shaded visual-novel art",
        },
        force_refresh=True,
    )

    assert output is not None
    assert output.to_cogview_prompt() == VALID_FULL_BODY_PROMPT
    assert call_llm.await_count == 2
    repair_fields = call_llm.await_args_list[1].args[1]
    assert repair_fields["validation_feedback"]
    assert "Half-body waist-up crop" in repair_fields["previous_invalid_output"]


@pytest.mark.asyncio
async def test_rewriter_allows_two_bounded_portrait_repairs(monkeypatch):
    service = PromptRewriterService()
    invalid_crop = RewrittenPrompt(
        final_prompt=VALID_FULL_BODY_PROMPT + " Half-body waist-up crop."
    )
    invalid_count = RewrittenPrompt(
        final_prompt=VALID_FULL_BODY_PROMPT.replace(
            "Exactly one isolated character",
            "A character",
        )
    )
    valid = RewrittenPrompt(final_prompt=VALID_FULL_BODY_PROMPT)
    call_llm = AsyncMock(side_effect=[invalid_crop, invalid_count, valid])

    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.PROMPT_REWRITER_ENABLED", True
    )
    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.api_key_available",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(service, "_call_llm", call_llm)

    output = await service.rewrite(
        "portrait",
        {
            "name": "林默",
            "appearance_zh": "black-haired adult male barista",
            "gender": "male",
            "emotion": "neutral",
            "pose": "standing",
            "shot": "full_body",
            "visual_style_prompt": "clean cel-shaded visual-novel art",
        },
        force_refresh=True,
    )

    assert output is not None
    assert output.final_prompt == VALID_FULL_BODY_PROMPT
    assert call_llm.await_count == 3
    assert call_llm.await_args_list[1].args[1]["repair_attempt"] == 1
    assert call_llm.await_args_list[2].args[1]["repair_attempt"] == 2


@pytest.mark.asyncio
async def test_generate_portrait_sends_llm_final_prompt_verbatim(monkeypatch):
    service = ImageGenerationService()
    captured = {}

    async def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "image_url": "/static/assets/portraits/test.png",
            "seed": 7,
            "size": [1024, 1536],
            "prompt": kwargs["prompt"],
        }

    monkeypatch.setattr(service, "generate_image", fake_generate_image)

    result = await service.generate_portrait(
        character_id="lin-mo",
        character_name="林默",
        appearance_prompt="black-haired adult male barista",
        emotion="neutral",
        pose="standing",
        seed=7,
        remove_bg=False,
        full_body=True,
        final_prompt=VALID_FULL_BODY_PROMPT,
        final_prompt_source="llm_rewriter",
        gender="male",
        visual_style_prompt="THIS BACKEND STYLE MUST NOT BE APPENDED",
    )

    assert result["success"] is True
    # style-lock 契约（44dab6e）：LLM final_prompt 本体逐字使用，
    # 但未内嵌风格锁时由后端确定性补前缀，防止渲染风格漂移。
    assert captured["prompt"] == (
        "THIS BACKEND STYLE MUST NOT BE APPENDED, " + VALID_FULL_BODY_PROMPT
    )
    assert captured["authoritative_prompt"] is True
    assert captured["width"] == 1024 and captured["height"] == 1536
    assert captured["prompt"].endswith(VALID_FULL_BODY_PROMPT)
    assert result["prompt"].endswith(VALID_FULL_BODY_PROMPT)
    assert result["prompt_source"] == "llm_rewriter"


@pytest.mark.asyncio
async def test_authoritative_prompt_reaches_provider_without_mutation(tmp_path, monkeypatch):
    service = ImageGenerationService()
    service.output_dir = tmp_path / "assets"
    service._init_output_dirs()
    provider_calls = []

    async def fake_provider(prompt, width, height, seed, **kwargs):
        provider_calls.append((prompt, width, height, seed, kwargs))
        return _png_bytes()

    monkeypatch.setattr(
        "app.services.image_generation_service.IMAGE_GENERATION_ENABLED", True
    )
    monkeypatch.setattr(
        "app.services.image_generation_service.AI_IMAGE_API_KEY", "fake-key"
    )
    monkeypatch.setattr(
        "app.services.image_generation_service.api_key_available",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(service, "_call_cogview_api", fake_provider)

    result = await service.generate_image(
        prompt=VALID_FULL_BODY_PROMPT,
        asset_type="portrait",
        width=768,
        height=1280,
        seed=9,
        ensure_portrait_alpha=False,
        authoritative_prompt=True,
    )

    assert result["success"] is True
    assert provider_calls[0][0] == VALID_FULL_BODY_PROMPT
    assert provider_calls[0][4]["retry_with_sanitized"] is False
    assert provider_calls[0][4]["authoritative_prompt"] is True
    assert result["prompt"] == VALID_FULL_BODY_PROMPT


@pytest.mark.asyncio
async def test_generate_portrait_rejects_conflict_before_provider(monkeypatch):
    service = ImageGenerationService()
    provider = AsyncMock()
    monkeypatch.setattr(service, "generate_image", provider)

    result = await service.generate_portrait(
        character_id="lin-mo",
        character_name="林默",
        appearance_prompt="black-haired adult male barista",
        full_body=True,
        final_prompt=VALID_FULL_BODY_PROMPT + " Half-body waist-up crop.",
        final_prompt_source="llm_rewriter",
    )

    assert result["success"] is False
    assert "一致性校验失败" in result["error"]
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_portrait_without_final_prompt_requires_llm(monkeypatch):
    service = ImageGenerationService()
    rewrite = AsyncMock(return_value=RewrittenPrompt(final_prompt=VALID_FULL_BODY_PROMPT))
    captured = {}

    async def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "image_url": "/static/assets/portraits/test.png",
            "seed": 7,
            "size": [1024, 1536],
            "prompt": kwargs["prompt"],
        }

    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.prompt_rewriter_service.rewrite",
        rewrite,
    )
    monkeypatch.setattr(service, "generate_image", fake_generate_image)

    result = await service.generate_portrait(
        character_id="lin-mo",
        character_name="林默",
        appearance_prompt="black-haired adult male barista",
        emotion="neutral",
        pose="standing",
        remove_bg=False,
        full_body=True,
        gender="male",
        visual_style_prompt="clean cel-shaded visual-novel art",
    )

    assert result["success"] is True
    fields = rewrite.await_args.args[1]
    assert fields["shot"] == "full_body"
    assert fields["visual_style_prompt"] == "clean cel-shaded visual-novel art"
    assert fields["subject_opening"] == "A young adult male character"
    assert fields["portrait_identity"] == {
        "kind": "generic",
        "appearance_prompt": "black-haired adult male barista",
    }
    assert "appearance_zh" not in fields
    assert captured["prompt"] == VALID_FULL_BODY_PROMPT


@pytest.mark.asyncio
async def test_unstamped_supplied_prompt_is_rewritten_instead_of_trusted(monkeypatch):
    service = ImageGenerationService()
    rewritten_prompt = VALID_FULL_BODY_PROMPT.replace("barista", "cafe owner")
    rewrite = AsyncMock(return_value=RewrittenPrompt(final_prompt=rewritten_prompt))
    captured = {}

    async def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "image_url": "/static/assets/portraits/test.png",
            "seed": 7,
            "size": [1024, 1536],
            "prompt": kwargs["prompt"],
        }

    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.prompt_rewriter_service.rewrite",
        rewrite,
    )
    monkeypatch.setattr(service, "generate_image", fake_generate_image)

    result = await service.generate_portrait(
        character_id="lin-mo",
        character_name="林默",
        appearance_prompt="black-haired adult male barista",
        remove_bg=False,
        full_body=True,
        final_prompt=VALID_FULL_BODY_PROMPT,
        # Deliberately omit final_prompt_source.
    )

    assert result["success"] is True
    assert rewrite.await_args.args[1]["draft_prompt"] == VALID_FULL_BODY_PROMPT
    assert captured["prompt"] == rewritten_prompt


@pytest.mark.asyncio
async def test_generate_portrait_does_not_fallback_when_llm_is_unavailable(monkeypatch):
    service = ImageGenerationService()
    provider = AsyncMock()
    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.prompt_rewriter_service.rewrite",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(service, "generate_image", provider)

    result = await service.generate_portrait(
        character_id="lin-mo",
        character_name="林默",
        appearance_prompt="black-haired adult male barista",
        full_body=True,
    )

    assert result["success"] is False
    assert "已停止生成" in result["error"]
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_portrait_builder_requests_full_body_from_llm(monkeypatch):
    captured_batch = []

    async def fake_rewrite_many(asset_type, batch):
        assert asset_type == "portrait"
        captured_batch.extend(batch)
        return [RewrittenPrompt(final_prompt=VALID_FULL_BODY_PROMPT) for _ in batch]

    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.prompt_rewriter_service.rewrite_many",
        fake_rewrite_many,
    )

    prompts = await prompt_builder_service.build_portrait_prompts_async(
        {
            "worldview": "当代咖啡店日常",
            "characters": [
                {
                    "name": "林默",
                    "gender": "男",
                    "appearance": "黑色短发，白衬衫，灰色长裤",
                }
            ],
        },
        project_id=21,
        variations=[
            {"emotion": "neutral", "outfit": "default", "pose": "standing"}
        ],
    )

    assert captured_batch[0]["shot"] == "full_body"
    assert captured_batch[0]["subject_opening"] == "A young adult male character"
    assert captured_batch[0]["portrait_identity"]["kind"] == "generic"
    assert "appearance_zh" not in captured_batch[0]
    assert "character_visual_profile" not in captured_batch[0]
    assert "canonical_identity" not in captured_batch[0]
    assert "character_visual_anchor" not in captured_batch[0]
    assert prompts[0]["final_prompt"] == VALID_FULL_BODY_PROMPT
    assert prompts[0]["final_prompt_source"] == "llm_rewriter"


def test_project_portrait_style_input_contains_no_framing_instruction():
    prompts = prompt_builder_service.build_portrait_prompts(
        {
            "worldview": "当代咖啡店日常",
            "characters": [
                {
                    "name": "林默",
                    "gender": "男",
                    "appearance": "黑色短发，白衬衫，灰色长裤",
                }
            ],
        },
        project_id=21,
        variations=[
            {"emotion": "neutral", "outfit": "default", "pose": "standing"}
        ],
    )
    style = prompts[0]["visual_style_prompt"].lower()
    assert "full-body" not in style and "full body" not in style
    assert "half-body" not in style and "half body" not in style


def test_portrait_rewriter_prompt_declares_concise_output_budget():
    service = PromptRewriterService()
    user_prompt = service._build_user_prompt(
        "portrait",
        {
            "name": "林默",
            "appearance_zh": "black-haired adult male barista",
            "shot": "full_body",
            "visual_style_prompt": "clean cel-shaded visual-novel art",
        },
    )

    assert "target 140-210 approximate tokens" in user_prompt
    assert "hard maximum 1120 ASCII characters" in user_prompt
    assert "exact literal phrase `exactly one isolated character`" in user_prompt


def test_portrait_rewriter_prompt_requires_identity_before_character_count():
    service = PromptRewriterService()
    user_prompt = service._build_user_prompt(
        "portrait",
        {
            "portrait_identity": {
                "kind": "canonical",
                "identity": {
                    "canonical_name": "Obito Uchiha",
                    "franchise_name": "Naruto",
                    "identity_prompt_en": "canonical young Obito Uchiha from Naruto",
                },
            },
            "subject_opening": "A teenage male Obito Uchiha from Naruto",
            "shot": "full_body",
        },
    )

    assert "first words of final_prompt MUST be exactly" in user_prompt
    assert "immediately after this opening, never before it" in user_prompt
